#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/time.h>
#include <signal.h>
#include <poll.h>
#include <fcntl.h>
#include <cstring>
#include "duckdb.hpp"
#include "yyjson.hpp"

namespace {

struct QueryTask {
    std::string id;
    std::string sql;
    int timeout_ms = 30000;
};

struct QueryResult {
    std::string id;
    std::string outcome;    // ok, parse_error, binder_error, runtime_error, internal_error, asan, crash, timeout
    int returncode = 0;
    std::string stdout_text;
    std::string stderr_text;
    double duration_ms = 0.0;
};

std::string escape_json(const std::string &s) {
    std::ostringstream o;
    for (char c : s) {
        switch (c) {
            case '"': o << "\\\""; break;
            case '\\': o << "\\\\"; break;
            case '\b': o << "\\b"; break;
            case '\f': o << "\\f"; break;
            case '\n': o << "\\n"; break;
            case '\r': o << "\\r"; break;
            case '\t': o << "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) <= 0x1f) {
                    o << "\\u00";
                    o << "0123456789abcdef"[(c >> 4) & 0xf];
                    o << "0123456789abcdef"[c & 0xf];
                } else {
                    o << c;
                }
        }
    }
    return o.str();
}

std::string result_to_json(const QueryResult &r) {
    std::string out = "{\"id\":\"" + escape_json(r.id) +
                      "\",\"outcome\":\"" + escape_json(r.outcome) +
                      "\",\"returncode\":" + std::to_string(r.returncode) +
                      ",\"stdout\":\"" + escape_json(r.stdout_text) +
                      "\",\"stderr\":\"" + escape_json(r.stderr_text) +
                      "\",\"duration_ms\":" + std::to_string(r.duration_ms) + "}\n";
    return out;
}

std::string classify_error_string(const std::string &err) {
    if (err.find("INTERNAL Error") != std::string::npos ||
        err.find("Assertion failed") != std::string::npos ||
        err.find("Assertion `") != std::string::npos) {
        return "internal_error";
    }
    if (err.find("INTERRUPT Error") != std::string::npos) {
        return "timeout";
    }
    if (err.find("Parser Error") != std::string::npos ||
        err.find("Syntax error") != std::string::npos) {
        return "parse_error";
    }
    if (err.find("Binder Error") != std::string::npos ||
        err.find("Catalog Error") != std::string::npos) {
        return "binder_error";
    }
    return "runtime_error";
}

bool has_asan_error(const std::string &text) {
    return text.find("Sanitizer") != std::string::npos ||
           text.find("runtime error:") != std::string::npos ||
           text.find("AddressSanitizer") != std::string::npos ||
           text.find("UndefinedBehaviorSanitizer") != std::string::npos;
}

// Set non-blocking mode on fd
void set_nonblocking(int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    }
}

// Drain available bytes from fd
std::string drain_fd(int fd) {
    set_nonblocking(fd);
    std::string out;
    char buf[4096];
    while (true) {
        ssize_t n = read(fd, buf, sizeof(buf));
        if (n > 0) {
            out.append(buf, n);
        } else {
            break;
        }
    }
    return out;
}

// RAII helper for catalog isolation
struct CatalogIsolation {
    duckdb::Connection &con;
    std::string schema_name;
    bool active = false;

    CatalogIsolation(duckdb::Connection &c, const std::string &name, bool isolate)
        : con(c), schema_name(name) {
        if (isolate) {
            con.Query("ATTACH ':memory:' AS " + schema_name + ";");
            con.Query("USE " + schema_name + ";");
            active = true;
        }
    }

    ~CatalogIsolation() {
        if (active) {
            con.Query("USE memory;");
            con.Query("DETACH " + schema_name + ";");
        }
    }
};

QueryResult run_task_in_worker(duckdb::DuckDB &db, const QueryTask &task, bool isolate, bool capture_stdout) {
    QueryResult qr;
    qr.id = task.id;

    auto start_time = std::chrono::steady_clock::now();

    duckdb::Connection con(db);
    std::atomic<bool> query_running{true};
    std::mutex cv_mtx;
    std::condition_variable cv;

    std::thread watchdog([&]() {
        std::unique_lock<std::mutex> lk(cv_mtx);
        if (!cv.wait_for(lk, std::chrono::milliseconds(task.timeout_ms), [&] { return !query_running.load(); })) {
            con.Interrupt();
        }
    });

    try {
        CatalogIsolation iso(con, "fuzz_isolate", isolate);

        std::vector<duckdb::unique_ptr<duckdb::SQLStatement>> statements;
        try {
            statements = con.ExtractStatements(task.sql);
        } catch (const std::exception &ex) {
            qr.outcome = "parse_error";
            qr.stderr_text = ex.what();
            qr.returncode = 1;
            {
                std::lock_guard<std::mutex> lk(cv_mtx);
                query_running = false;
            }
            cv.notify_one();
            watchdog.join();

            auto end_time = std::chrono::steady_clock::now();
            qr.duration_ms = std::chrono::duration<double, std::milli>(end_time - start_time).count();
            return qr;
        }

        std::string internal_err = "";
        std::string timeout_err = "";
        std::string first_err = "";
        std::string first_err_kind = "";

        for (size_t i = 0; i < statements.size(); i++) {
            auto res = con.Query(std::move(statements[i]));
            if (res->HasError()) {
                std::string err = res->GetError();
                std::string kind = classify_error_string(err);
                if (kind == "internal_error" && internal_err.empty()) {
                    internal_err = err;
                } else if (kind == "timeout" && timeout_err.empty()) {
                    timeout_err = err;
                }
                if (first_err.empty()) {
                    first_err = err;
                    first_err_kind = kind;
                }
                qr.stderr_text += err + "\n";
            } else {
                if (capture_stdout) {
                    qr.stdout_text += res->ToString() + "\n";
                }
            }
        }

        if (!internal_err.empty()) {
            qr.outcome = "internal_error";
            qr.returncode = 1;
        } else if (!timeout_err.empty()) {
            qr.outcome = "timeout";
            qr.returncode = -1;
        } else if (!first_err.empty()) {
            qr.outcome = first_err_kind;
            qr.returncode = (first_err_kind == "timeout" ? -1 : 1);
        } else {
            qr.outcome = "ok";
            qr.returncode = 0;
        }
    } catch (const std::exception &ex) {
        std::string err = ex.what();
        qr.stderr_text = err;
        qr.outcome = classify_error_string(err);
        qr.returncode = 1;
    }

    {
        std::lock_guard<std::mutex> lk(cv_mtx);
        query_running = false;
    }
    cv.notify_one();
    watchdog.join();

    auto end_time = std::chrono::steady_clock::now();
    qr.duration_ms = std::chrono::duration<double, std::milli>(end_time - start_time).count();
    return qr;
}

void run_worker_loop(const std::string &db_path, bool isolate, bool capture_stdout) {
    // Ensure standard sanitizers do not terminate silently
    setenv("ASAN_OPTIONS", "detect_leaks=0:abort_on_error=1:symbolize=1", 0);
    setenv("UBSAN_OPTIONS", "print_stacktrace=1:halt_on_error=1", 0);

    duckdb::DuckDB db(db_path.empty() ? nullptr : db_path.c_str());

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;

        QueryTask task;
        task.id = "query";
        task.timeout_ms = 30000;

        duckdb_yyjson::yyjson_doc *doc = duckdb_yyjson::yyjson_read(line.c_str(), line.length(), 0);
        if (doc) {
            duckdb_yyjson::yyjson_val *root = duckdb_yyjson::yyjson_doc_get_root(doc);
            if (root) {
                duckdb_yyjson::yyjson_val *v_id = duckdb_yyjson::yyjson_obj_get(root, "id");
                duckdb_yyjson::yyjson_val *v_sql = duckdb_yyjson::yyjson_obj_get(root, "sql");
                duckdb_yyjson::yyjson_val *v_timeout = duckdb_yyjson::yyjson_obj_get(root, "timeout_ms");

                if (v_id && duckdb_yyjson::yyjson_is_str(v_id)) {
                    task.id = duckdb_yyjson::yyjson_get_str(v_id);
                }
                if (v_sql && duckdb_yyjson::yyjson_is_str(v_sql)) {
                    task.sql = duckdb_yyjson::yyjson_get_str(v_sql);
                }
                if (v_timeout && duckdb_yyjson::yyjson_is_int(v_timeout)) {
                    task.timeout_ms = duckdb_yyjson::yyjson_get_int(v_timeout);
                }
            }
            duckdb_yyjson::yyjson_doc_free(doc);
        } else {
            // Raw SQL fallback
            task.sql = line;
        }

        QueryResult res = run_task_in_worker(db, task, isolate, capture_stdout);
        std::string json_str = result_to_json(res);
        std::cout << json_str;
        std::cout.flush();
    }
}

// Supervisor manages worker process lifecycle
class Supervisor {
private:
    std::string exe_path;
    std::string db_path;
    bool isolate;
    bool capture_stdout;
    int batch_size;
    int queries_in_worker = 0;

    pid_t worker_pid = -1;
    int pipe_to_worker[2] = {-1, -1};
    int pipe_from_worker[2] = {-1, -1};
    int pipe_err_worker[2] = {-1, -1};

    void stop_worker() {
        if (worker_pid > 0) {
            close(pipe_to_worker[1]);
            int status;
            pid_t p = waitpid(worker_pid, &status, WNOHANG);
            if (p == 0) {
                kill(worker_pid, SIGTERM);
                usleep(50000);
                p = waitpid(worker_pid, &status, WNOHANG);
                if (p == 0) {
                    kill(worker_pid, SIGKILL);
                    waitpid(worker_pid, &status, 0);
                }
            }
            close(pipe_from_worker[0]);
            close(pipe_err_worker[0]);
            worker_pid = -1;
        }
    }

    void start_worker() {
        if (pipe(pipe_to_worker) < 0 || pipe(pipe_from_worker) < 0 || pipe(pipe_err_worker) < 0) {
            std::cerr << "Failed to create pipes for worker" << std::endl;
            exit(1);
        }

        worker_pid = fork();
        if (worker_pid < 0) {
            std::cerr << "Failed to fork worker process" << std::endl;
            exit(1);
        }

        if (worker_pid == 0) {
            // Worker child process
            close(pipe_to_worker[1]);
            close(pipe_from_worker[0]);
            close(pipe_err_worker[0]);

            dup2(pipe_to_worker[0], STDIN_FILENO);
            dup2(pipe_from_worker[1], STDOUT_FILENO);
            dup2(pipe_err_worker[1], STDERR_FILENO);

            close(pipe_to_worker[0]);
            close(pipe_from_worker[1]);
            close(pipe_err_worker[1]);

            run_worker_loop(db_path, isolate, capture_stdout);
            _exit(0);
        }

        // Supervisor parent process
        close(pipe_to_worker[0]);
        close(pipe_from_worker[1]);
        close(pipe_err_worker[1]);
        queries_in_worker = 0;
    }

public:
    Supervisor(const std::string &exe, const std::string &db, bool iso, bool cap_out, int bsize)
        : exe_path(exe), db_path(db), isolate(iso), capture_stdout(cap_out), batch_size(bsize) {
        start_worker();
    }

    ~Supervisor() {
        stop_worker();
    }

    void execute_task(const QueryTask &task) {
        if (worker_pid <= 0 || (batch_size > 0 && queries_in_worker >= batch_size)) {
            stop_worker();
            start_worker();
        }

        queries_in_worker++;
        auto start_t = std::chrono::steady_clock::now();

        std::string req = "{\"id\":\"" + escape_json(task.id) + "\",\"sql\":\"" +
                          escape_json(task.sql) + "\",\"timeout_ms\":" +
                          std::to_string(task.timeout_ms) + "}\n";

        ssize_t written = write(pipe_to_worker[1], req.c_str(), req.length());
        if (written <= 0) {
            // Worker pipe broken
            stop_worker();
            start_worker();
            written = write(pipe_to_worker[1], req.c_str(), req.length());
        }

        // Wait for response with supervisor watchdog
        int wait_timeout_ms = task.timeout_ms + 2000;
        struct pollfd pfd;
        pfd.fd = pipe_from_worker[0];
        pfd.events = POLLIN;

        int poll_ret = poll(&pfd, 1, wait_timeout_ms);
        if (poll_ret > 0 && (pfd.revents & POLLIN)) {
            // Worker responded
            std::string line;
            char ch;
            while (read(pipe_from_worker[0], &ch, 1) > 0) {
                line.push_back(ch);
                if (ch == '\n') break;
            }

            if (!line.empty()) {
                std::cout << line;
                std::cout.flush();
                return;
            }
        }

        // Worker either crashed or timed out hard
        auto end_t = std::chrono::steady_clock::now();
        double elapsed_ms = std::chrono::duration<double, std::milli>(end_t - start_t).count();

        QueryResult crash_res;
        crash_res.id = task.id;
        crash_res.duration_ms = elapsed_ms;

        int status = 0;
        pid_t p = waitpid(worker_pid, &status, WNOHANG);

        if (poll_ret == 0 && p == 0) {
            // Worker hung hard beyond interrupt -> kill with SIGKILL
            kill(worker_pid, SIGKILL);
            waitpid(worker_pid, &status, 0);
            crash_res.outcome = "timeout";
            crash_res.returncode = -1;
            crash_res.stderr_text = "Query timed out (killed by supervisor)";
        } else {
            // Worker died / crashed
            std::string stderr_dump = drain_fd(pipe_err_worker[0]);
            crash_res.stderr_text = stderr_dump;

            if (WIFSIGNALED(status)) {
                int sig = WTERMSIG(status);
                crash_res.returncode = -sig;
                if (has_asan_error(stderr_dump)) {
                    crash_res.outcome = "asan";
                } else {
                    crash_res.outcome = "crash";
                }
            } else if (WIFEXITED(status)) {
                crash_res.returncode = WEXITSTATUS(status);
                if (has_asan_error(stderr_dump)) {
                    crash_res.outcome = "asan";
                } else {
                    crash_res.outcome = "crash";
                }
            } else {
                crash_res.outcome = "crash";
                crash_res.returncode = 1;
            }
        }

        // Print result for crashed query
        std::cout << result_to_json(crash_res);
        std::cout.flush();

        // Respawn clean worker for next queries
        stop_worker();
        start_worker();
    }
};

} // namespace

int main(int argc, char **argv) {
    std::string batch_file = "";
    std::string sql_file = "";
    std::string db_path = "";
    float timeout_sec = 30.0f;
    bool isolate = true;
    bool capture_stdout = false;
    bool worker_mode = false;
    int batch_size = 1000;

    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--batch" && i + 1 < argc) {
            batch_file = argv[++i];
        } else if (arg == "--sql" && i + 1 < argc) {
            sql_file = argv[++i];
        } else if (arg == "--db" && i + 1 < argc) {
            db_path = argv[++i];
        } else if (arg == "--timeout" && i + 1 < argc) {
            timeout_sec = std::stof(argv[++i]);
        } else if (arg == "--batch-size" && i + 1 < argc) {
            batch_size = std::stoi(argv[++i]);
        } else if (arg == "--isolate") {
            isolate = true;
        } else if (arg == "--no-isolate") {
            isolate = false;
        } else if (arg == "--output" || arg == "--capture-stdout") {
            capture_stdout = true;
        } else if (arg == "--worker") {
            worker_mode = true;
        } else if (arg == "-h" || arg == "--help") {
            std::cout << "Usage: driver [options] [script.sql]\n"
                      << "Options:\n"
                      << "  --batch <file.jsonl>  Execute queries from JSONL file\n"
                      << "  --sql <file.sql>      Execute single SQL file\n"
                      << "  --timeout <seconds>   Per-query timeout (default 30.0)\n"
                      << "  --db <path>           Target database (default :memory:)\n"
                      << "  --isolate             Catalog isolation via attach/detach (default: on)\n"
                      << "  --no-isolate          Disable catalog isolation (fastest, shared state)\n"
                      << "  --batch-size <N>      Recycle worker process after N queries (default 1000)\n"
                      << "  --capture-stdout      Capture tabular result stdout\n"
                      << "  --worker              Run directly in worker mode (no supervisor)\n";
            return 0;
        } else if (arg[0] != '-' && sql_file.empty()) {
            sql_file = arg;
        }
    }

    if (worker_mode) {
        run_worker_loop(db_path, isolate, capture_stdout);
        return 0;
    }

    Supervisor sup(argv[0], db_path, isolate, capture_stdout, batch_size);
    int default_timeout_ms = static_cast<int>(timeout_sec * 1000.0f);

    if (!sql_file.empty()) {
        std::ifstream f(sql_file);
        if (!f.is_open()) {
            std::cerr << "Failed to open SQL file: " << sql_file << std::endl;
            return 1;
        }
        std::stringstream ss;
        ss << f.rdbuf();
        QueryTask t;
        t.id = sql_file;
        t.sql = ss.str();
        t.timeout_ms = default_timeout_ms;
        sup.execute_task(t);
        return 0;
    }

    std::istream *input_stream = &std::cin;
    std::ifstream batch_in;
    if (!batch_file.empty()) {
        batch_in.open(batch_file);
        if (!batch_in.is_open()) {
            std::cerr << "Failed to open batch file: " << batch_file << std::endl;
            return 1;
        }
        input_stream = &batch_in;
    }

    std::string line;
    int idx = 0;
    while (std::getline(*input_stream, line)) {
        if (line.empty()) continue;
        QueryTask t;
        t.id = "q_" + std::to_string(++idx);
        t.timeout_ms = default_timeout_ms;

        if (line[0] == '{') {
            duckdb_yyjson::yyjson_doc *doc = duckdb_yyjson::yyjson_read(line.c_str(), line.length(), 0);
            if (doc) {
                duckdb_yyjson::yyjson_val *root = duckdb_yyjson::yyjson_doc_get_root(doc);
                if (root) {
                    duckdb_yyjson::yyjson_val *v_id = duckdb_yyjson::yyjson_obj_get(root, "id");
                    duckdb_yyjson::yyjson_val *v_sql = duckdb_yyjson::yyjson_obj_get(root, "sql");
                    duckdb_yyjson::yyjson_val *v_timeout = duckdb_yyjson::yyjson_obj_get(root, "timeout_ms");

                    if (v_id && duckdb_yyjson::yyjson_is_str(v_id)) {
                        t.id = duckdb_yyjson::yyjson_get_str(v_id);
                    }
                    if (v_sql && duckdb_yyjson::yyjson_is_str(v_sql)) {
                        t.sql = duckdb_yyjson::yyjson_get_str(v_sql);
                    }
                    if (v_timeout && duckdb_yyjson::yyjson_is_int(v_timeout)) {
                        t.timeout_ms = duckdb_yyjson::yyjson_get_int(v_timeout);
                    }
                }
                duckdb_yyjson::yyjson_doc_free(doc);
            }
        } else {
            t.sql = line;
        }

        sup.execute_task(t);
    }

    return 0;
}
