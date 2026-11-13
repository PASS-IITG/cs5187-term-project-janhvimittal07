from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
import sys
NAVY=RGBColor(0x1F,0x2A,0x44); ACC=RGBColor(0xE0,0x7A,0x1F); GRAY=RGBColor(0x44,0x44,0x44); WHITE=RGBColor(255,255,255)
p=Presentation(); p.slide_width=Inches(13.333); p.slide_height=Inches(7.5); B=p.slide_layouts[6]
def box(s,x,y,w,h,text,size=18,color=GRAY,bold=False,fill=None,align=None):
    sh=s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h)) if fill else s.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
    if fill: sh.fill.solid(); sh.fill.fore_color.rgb=fill; sh.line.fill.background()
    tf=sh.text_frame; tf.word_wrap=True
    for i,line in enumerate(text.split("\n")):
        para=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        r=para.add_run(); r.text=line; r.font.size=Pt(size); r.font.bold=bold; r.font.color.rgb=color
        if align: para.alignment=align
    return sh
def slide(title,bullets=(),novel=False,size=20):
    s=p.slides.add_slide(B)
    bar=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,p.slide_width,Inches(1.1)); bar.fill.solid(); bar.fill.fore_color.rgb=NAVY; bar.line.fill.background()
    box(s,0.5,0.2,10.5,0.8,title,30,WHITE,True)
    if novel: box(s,11.2,0.3,1.7,0.55,"NOVEL",18,WHITE,True,ACC,2)
    if bullets: box(s,0.6,1.4,12.1,5.8,"\n".join("• "+b for b in bullets),size)
    return s
# 1 title
s=p.slides.add_slide(B); bg=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,p.slide_width,p.slide_height); bg.fill.solid(); bg.fill.fore_color.rgb=NAVY; bg.line.fill.background()
box(s,0.8,2.0,11.7,1.5,"Semantic LLM-Directed Fuzzing of DuckDB",44,WHITE,True)
box(s,0.8,3.5,11.7,1,"Finding silent wrong-answer bugs in a SQL optimizer",24,ACC)
box(s,0.8,5.0,11.7,1.2,"Prabhnoor Singh · Niket Garg · Janhvi Mittal\nCS5187 Term Project · IIT Guwahati",20,WHITE)
slide("The problem",["Optimizer bugs give WRONG ANSWERS silently: no crash, no error, users trust the result",
 "Random SQL fuzzers rarely reach deep optimizer rules: the rule only fires under narrow conditions",
 "Rewrites fail at edge values: type limits, -0.0, infinity, extreme dates, out-of-range constants",
 "Goal: aim fuzzing at specific optimizer rules, prove we reached them, and catch wrong results automatically"])
# evolution
s=slide("Not one method: four methods run in parallel")
ph=[("Method A","Classic fuzzing\n(baseline)","SQLsmith + ASan, differential sweeps, closed-issue replay","0 new bugs\n(only known ones)"),
("Method B","Feature-targeted fuzzers","New DuckDB 2.0 features: triggers, LATERAL, delim-join rewrite","#27759 #27850\n#27904 #28052"),
("Method C","New-code hints +\nLLM generators","Hints, feature cards and fix dossiers guide the LLM (team, Gemini)","Validated pipeline;\nre-found #27759"),
("Method D","Rule-targeted\nhunt loop","Rule cards + hypothesis-first Claude generators, automated","12 issues\n#28527 … #28566")]
for i,(a,b,c,d) in enumerate(ph):
    x=0.45+i*3.15
    box(s,x,1.5,2.95,0.6,a,20,WHITE,True,ACC if i>=2 else NAVY,2)
    box(s,x,2.2,2.95,1.0,b,19,NAVY,True,None,2)
    box(s,x,3.25,2.95,1.6,c,15,GRAY,False,None,2)
    box(s,x,5.0,2.95,1.1,d,17,NAVY,True,RGBColor(0xEE,0xEE,0xEE),2)
box(s,0.6,6.4,12,0.8,"All four run side by side and share one judge, oracle, triage and known-issues registry. Each covers a different blind spot.",16,GRAY)
slide("Method A: Classic fuzzing (baseline, always on)",["SQLsmith campaign on an ASan + UBSan build of DuckDB 2.0.0: 70,980 queries, 0 findings",
 "Differential sweep over 1,959 seed scripts (optimizer on vs off): 2 real crashes, both already known upstream",
 "Replayed reproducers mined from closed issues: only re-found bugs already fixed on main",
 "Extra generators: bottom-up (SQLBull-style), window functions, join equivalence (JoinEquiv-style), functions",
 "Role: a baseline and a crash safety net; it shows generic fuzzing alone mostly re-finds known bugs"])
slide("Method B: Fuzzers aimed at new features",["Focus on code that is new in DuckDB 2.0 and rarely reported: triggers, LATERAL joins, delim_join_as_cte rewrite",
 "Trigger fuzzer with emulation / persistence / detection oracles → #27759 (first new bug, reproduced by maintainers)",
 "delim_join_as_cte on vs off differential → #27850 (fixed on main by #28144)",
 "Niket: LATERAL + filter pushdown → #27904 (reproduced)",
 "Optimizer rewrite with a NaN constant → #28052 (wrong result, fixed by #28071)"])
slide("Method C: New-code hints guide LLM generators",["New-code hints: diff DuckDB 1.5 → 2.0 and point the LLM at code that is new and barely tested",
 "Feature cards + fix-diff dossiers: what the feature does, and the code around past fixes",
 "Contract: the LLM writes a small generator program, not single queries",
 "Engine-judged: valid rate, reach (EXPLAIN plan changes when the target switch flips), plan-shape diversity",
 "One-command 12-hour resumable hunt (targeted_run) run by teammates on Gemini",
 "Red-team review: agents game any metric → judge rebuilt on real operator trees, known-issues registry, audit gate",
 "Janhvi: Round 2 with 12 directed generators, AUDIT PASSED; the team run independently re-found #27759",
 "Same judge, registry and audit as Method D"],True,17)
# method diagram
s=slide("Method D: the rule-targeted pipeline")
steps=["DuckDB source\n+ git fix history","Rule cards","LLM writes a\ntest generator","Engine judge:\ndid the rule fire?","Differential oracle\nrule ON vs OFF","Auto triage","Human review\n→ GitHub issue"]
w=1.62
for i,t in enumerate(steps):
    x=0.35+i*(w+0.2); box(s,x,2.4,w,1.6,t,15,WHITE,True,ACC if i in(1,2,3) else NAVY,2)
    if i<len(steps)-1:
        a=s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW,Inches(x+w+0.01),Inches(3.05),Inches(0.18),Inches(0.3)); a.fill.solid(); a.fill.fore_color.rgb=GRAY; a.line.fill.background()
box(s,0.6,4.6,12,2.4,"Orange boxes = our novel steps.\nRuns unattended for hours: ledger resume, crash supervisor, RAM guard, keep-awake.\nThe LLM proposes; the engine decides; a human reads every candidate before anything is filed.",18)
slide("Step 1: Rule cards from code and fix history",["For every optimizer rule, extracted automatically:",
 "   recent FIX commits + the tests they added: the next bug sits next to the last fix",
 "   every early exit / condition under which the rewrite fires",
 "   every place the rule builds or converts a value (bounds, folded constants, casts)",
 "   its marker functions and the switch that turns it off",
 "Targets ranked by a score: fixes, commits, value-building sites"],True)
slide("Step 2: Hypothesis-first LLM generation",["The LLM (Claude, headless) reads the real DuckDB source with read-only tools",
 "It must state a bug HYPOTHESIS and PRECONDITIONS first; generators without them are rejected",
 "It writes a Python GENERATOR program, not single queries: hundreds of varied queries per round",
 "Inputs: constants taken from the table rows + 275 type-limit literals over 30 types",
 "Target pattern placed in WHERE, SELECT, CASE, FILTER, JOIN ON, subqueries",
 "Sandboxed: empty working folder, never sees our issues, drafts or controls"],True)
slide("Step 3: The engine is the judge",["We do not trust the LLM's claims: each generator is RUN on DuckDB",
 "Rule-level reach: a marker call must DISAPPEAR from the plan, proving the rewrite fired",
 "Plan-shape diversity and validity rate are measured too",
 "Negative control: a 'cheating' generator that names the rule but never triggers it is rejected",
 "Positive controls: old generators must re-find our earlier bugs after every pipeline change"],True)
slide("Step 4–5: Oracle and automatic triage",["Oracle: same query with the rule ON vs OFF (SET disabled_optimizers=...), 5 configurations",
 "Different rows → candidate wrong result; error only when optimized → candidate error bug",
 "False-positive filters: unique ORDER BY keys, no unordered LIMIT, float noise near zero",
 "Triage: re-verify → known-issues registry → delta-minimize → newest main build → GitHub duplicate search",
 "Output: CANDIDATES.md with a minimal reproducer, read by a human"])
slide("What is new in our approach",["1. Targets mined from code AND fix history (rule cards)",
 "2. LLM must state a hypothesis before generating",
 "3. LLM writes generators, not test cases: scale without more LLM calls",
 "4. Execution-verified rule reach as the judge, not LLM self-report",
 "5. Positive / negative controls that rediscover our own past bugs",
 "6. Fully automated multi-hour loop with resume and safety guards",
 "7. Several complementary methods in parallel, sharing one judge, oracle and triage"],True,21)
slide("Results",["16 issues filed on github.com/duckdb/duckdb (15 Prabhnoor, 1 Niket)",
 "Most are SILENT WRONG RESULTS in optimizer rewrites",
 "Bug kinds: 8 wrong results, 5 INTERNAL errors / crashes, 3 spurious conversion errors",
 "2 fixed on main (#28144, #28071); fix PRs open for 7 more, including our own #28547",
 "Automated hunt: 40 LLM calls over 13 targets → 9 candidates → new issues #28559, #28562, #28565, #28566"],size=22)
top=[("#1  #28052  Wrong result: negated DOUBLE vs NaN  [FIXED, PR #28071 merged]","The move-constants rewrite turns -x = c into x = -c. With a NaN constant this changes the answer, so the optimized query returns wrong rows. Maintainers confirmed it and merged the fix."),
("#2  #27850  INTERNAL Error: identical EXISTS under UNION ALL  [FIXED on main, #28144]","Two identical EXISTS subqueries under UNION ALL with delim_join_as_cte=false crash the planner with an INTERNAL Error. Fixed on main."),
("#3  #27759  INTERNAL Error: trigger cascade  [REPRODUCED, fix PR #27839]","Our first new bug. A FOR EACH ROW trigger that inserts into a table with a BEFORE INSERT trigger fails with 'Conflicting or cyclic column binding replacement'. Found by the trigger fuzzer on a brand-new 2.0 feature."),
("#4  #28541  Silent wrong result: UHUGEINT constants wrap  [fix PR #28549 open]","In uhugeint_col + c1 = c2, a constant above the HUGEINT maximum wraps around when the optimizer moves constants across '='. Wrong rows with no error at all."),
("#5  #28532  INTERNAL Error + database invalidated  [partial fix PR #28539]","Casting TIMESTAMP_MS to TIMESTAMP_S at the end of the range raises an INTERNAL Error that invalidates the database: every later query in the session fails.")]
for k in (0,3):
    s=slide("Top 5 bugs ("+("1–3" if k==0 else "4–5")+")")
    for j,(t,d) in enumerate(top[k:k+3]):
        y=1.4+j*1.95; box(s,0.6,y,12.1,0.55,t,19,NAVY,True); box(s,0.6,y+0.55,12.1,1.3,d,17)
bugs=[('27759', 'INTERNAL Error: FOR EACH ROW trigger inserts into a table with a BEFORE INSERT trigger', 'Reproduced; fix PR #27839'), ('27850', 'INTERNAL Error: identical EXISTS blocks under UNION ALL (delim_join_as_cte=false)', 'Closed, fixed (#28144)'), ('27904', 'INTERNAL Error: CROSS JOIN LATERAL with nested LATERAL count(*)', 'Reproduced, no fix yet'), ('28052', 'Wrong result: negated DOUBLE compared with NaN constant', 'Closed, PR #28071 merged'), ('28527', 'Wrong result: date_trunc with sub-day part on a DATE column', 'Fix PR #28528 open'), ('28531', 'Conversion error: CAST(timestamp AS DATE) = date at range limits', 'Our fix PR #28547'), ('28532', 'INTERNAL Error + DB invalidated: TIMESTAMP_MS to TIMESTAMP_S at range end', 'Partial fix PR #28539'), ('28534', 'Conversion error: date_trunc comparisons near TIMESTAMP range end', 'Raised; our fix ready'), ('28535', 'INTERNAL Error: UBIGINT constants above BIGINT max in x + c1 = c2', 'Fix PR #28540 open'), ('28536', 'Wrong result: regex empty group (?:) at edge treated as anchor', 'Fix PR #28538 open'), ('28541', 'Silent wrong result: UHUGEINT constants wrap in x + c1 = c2', 'Fix PR #28549 open'), ('28542', 'TRY_CAST(date AS TIMESTAMP) comparison: false instead of NULL', 'Raised'), ('28559', 'Wrong result: Top-N window elimination, 0.0 / -0.0 keys', 'Raised'), ('28562', "Wrong result: contains(list, '') true without empty string", 'Raised'), ('28565', 'Conversion error: CASE guard around STRUCT field cast ignored', 'Raised'), ('28566', 'Wrong result: DOUBLE[] join key sides merged for [0.0] / [-0.0]', 'Raised')]
s=slide("All 16 issues filed")
tb=s.shapes.add_table(len(bugs)+1,3,Inches(0.6),Inches(1.25),Inches(12.1),Inches(6)).table
for c,(h,wd) in enumerate([("Issue",0.9),("What",8.3),("Status",2.9)]):
    tb.columns[c].width=Inches(wd); tb.cell(0,c).text=h
for r,row in enumerate(bugs,1):
    for c,v in enumerate(row): tb.cell(r,c).text=("#"+v) if c==0 else v
for r in range(len(bugs)+1):
    tb.rows[r].height=Inches(0.33)
    for c in range(3):
        for para in tb.cell(r,c).text_frame.paragraphs:
            for run in para.runs: run.font.size=Pt(11)
slide("Limits and next steps",["Finds are mostly one family: constants at type limits in optimizer rewrites",
 "Returns per target shrink: rotating targets beats longer runs on one rule",
 "Operator rules (+, LIKE) have no marker function, so their reach is measured more weakly",
 "Next: new target sources (statistics propagation, filter pruning, precision casts)",
 "Next: other engines (PostgreSQL); aim for ~30 bugs → paper"])
s=p.slides.add_slide(B); bg=s.shapes.add_shape(MSO_SHAPE.RECTANGLE,0,0,p.slide_width,p.slide_height); bg.fill.solid(); bg.fill.fore_color.rgb=NAVY; bg.line.fill.background()
box(s,0.8,2.8,11.7,1.5,"Thank you — Questions?",44,WHITE,True,None,2)
p.save(sys.argv[1])
