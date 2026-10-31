# Formulas and where they come from

Every number Handoff shows comes from one of the formulas below. Some come from published research, and some are our own documented choices. This page says which is which, so nobody overclaims in a demo or Q&A.

---

## 1. Which parts are research-backed

| Part | Source | How solid |
|---|---|---|
| **DOA formula** (3.293, 1.098, 0.164, −0.321) | Research: Fritz et al. [1, 2] fitted these coefficients in their "degree-of-knowledge" studies of how well developers know code | ✅ Published, but fitted on a fairly small study of professional developers, not open source |
| **Author thresholds** (75% of the top score, and at least 3.293) | Research: Avelino et al. [3], who used DOA to estimate truck factors across GitHub projects | ✅ Published |
| **Truck-factor algorithm** (remove top experts until fewer than half the files have one) | Research: Avelino et al. [3]. They checked their results against what developers of real projects said | ✅ Published and validated |
| **Importance weights** (0.4 size, 0.4 fan-in, 0.2 recent work) | **Ours** | ⚠️ A reasonable heuristic, but untested. Each ingredient makes sense on its own (bigger and more depended-on code is riskier to lose), but the exact weights are a judgment call |
| **Module risk** (orphaned files count fully, at-risk files count half) | **Ours** | ⚠️ A heuristic |
| **Successor rule** (highest score, active in the last 12 months) | **Ours** | ⚠️ A heuristic |
| **Filters** (bulk commits over 100 files, bots) | **Ours**, but this is common practice in this kind of research | ⚠️ A sensible default, and adjustable (`--bulk-limit`) |

**How to say this to judges:** "Who knows what, and the truck factor, come from published research. We didn't invent those numbers. Ranking which files matter most is our own documented heuristic, kept deliberately simple. Validating it, for example against CODEOWNERS files, is future work."

### References (verified against the Avelino et al. paper)

1. T. Fritz, J. Ou, G. C. Murphy, E. Murphy-Hill. "A degree-of-knowledge model to capture source code familiarity." *ICSE 2010*, pp. 385–394.
2. T. Fritz, G. C. Murphy, E. Murphy-Hill, J. Ou, E. Hill. "Degree-of-knowledge: modeling a developer's knowledge of code." *ACM TOSEM* 23(2), 14:1–14:42, 2014.
3. G. Avelino, L. Passos, A. Hora, M. T. Valente. "A novel approach for estimating truck factors." *ICPC 2016*. [arXiv:1604.06766](https://arxiv.org/abs/1604.06766)

The DOA formula and its coefficients come from [1, 2]. Avelino et al. [3] normalised it, set the author thresholds (normalised DOA > 0.75 and DOA ≥ 3.293, tuned against `git blame` rankings) and defined the greedy truck-factor algorithm, which they tested on 133 GitHub projects.

---

## 2. Degree of Authorship (DOA)

**Question:** does developer $d$ know file $f$? *(research-backed)*

$$
\mathrm{DOA}(d, f) = 3.293 + 1.098\,\mathrm{FA} + 0.164\,\mathrm{DL} - 0.321\,\ln(1 + \mathrm{AC})
$$

where

- $\mathrm{FA} = 1$ if $d$ created $f$, otherwise $0$;
- $\mathrm{DL}$ is the number of commits by $d$ to $f$;
- $\mathrm{AC}$ is the number of commits to $f$ by everyone else.

### Normalised DOA

Each score is divided by the best score on the same file:

$$
\mathrm{DOA}_N(d, f) = \frac{\mathrm{DOA}(d, f)}{\max_{d'} \mathrm{DOA}(d', f)}
$$

If that maximum is $\le 0$, every $\mathrm{DOA}_N$ on the file is set to $0$, so a negative maximum can't flip the signs.

### Who counts as an author

$$
d \text{ is an author of } f \iff \mathrm{DOA}_N(d, f) > 0.75 \;\land\; \mathrm{DOA}(d, f) \ge 3.293
$$

---

## 3. Departure impact

Let $F$ be the set of source files at HEAD, $A(f)$ the set of authors of $f$, and $X$ the developer who leaves.

### At-risk and orphaned files

$$
\text{AtRisk}(X) = \{\, f \in F : X \in A(f) \,\}
\qquad
\text{Orphaned}(X) = \{\, f \in F : A(f) = \{X\} \,\}
$$

### Coverage

The share of files that still have at least one author once the developers in $S$ are gone:

$$
\mathrm{Coverage}(S) = \frac{\left|\{\, f \in F : A(f) \setminus S \neq \varnothing \,\}\right|}{|F|}
$$

The UI shows $\mathrm{Coverage}(\varnothing) \to \mathrm{Coverage}(\{X\})$.

### Truck factor *(research-backed)*

A greedy algorithm. Start with $S = \varnothing$ (or $S = \{X\}$ for "after $X$ leaves"):

1. While $\mathrm{Coverage}(S) \ge 0.5$: add to $S$ the remaining developer who is an author of the most files.
2. The truck factor is the number of developers added.

$$
\mathrm{TF} = \left|S_{\text{final}} \setminus S_{\text{start}}\right|
$$

---

## 4. Importance of a file

**Question:** how much does file $f$ matter? *(our heuristic)*

$$
I(f) = 0.4\,\widehat{\mathrm{LOC}}(f) + 0.4\,\widehat{\mathrm{FanIn}}(f) + 0.2\,\widehat{\mathrm{Recent}}_X(f)
$$

Each term is normalised to $[0, 1]$ by dividing by its maximum over the at-risk files:

$$
\widehat{v}(f) = \frac{v(f)}{\max_{g \in \text{AtRisk}(X)} v(g)}
\qquad (\text{and } 0 \text{ if that maximum is } 0)
$$

where

- $\mathrm{LOC}(f)$ is the number of non-blank lines;
- $\mathrm{FanIn}(f)$ is the number of other files that statically import $f$ (Python only);
- $\mathrm{Recent}_X(f)$ is the number of commits by $X$ to $f$ in the 12 months before the HEAD commit's date.

---

## 5. Module risk

A module is a folder $M$. *(our heuristic)*

$$
\mathrm{Risk}(M) = \sum_{f \in M \,\cap\, \text{Orphaned}(X)} I(f) \;+\; 0.5 \sum_{f \in M \,\cap\, (\text{AtRisk}(X) \setminus \text{Orphaned}(X))} I(f)
$$

The 5 highest-risk modules go on to KT generation.

---

## 6. Successor

*(our heuristic)* Among developers other than $X$ who committed in the 12 months before HEAD (the set $\text{Active}$), pick the one with the highest summed normalised DOA over the module's at-risk files:

$$
\mathrm{Successor}(M) = \arg\max_{d \in \text{Active} \setminus \{X\}} \; \sum_{f \in M \,\cap\, \text{AtRisk}(X)} \mathrm{DOA}_N(d, f)
$$

If no active developer has a positive sum, the module shows **none**.

---

## 7. Verified claims (generation half)

Not a formula from research, just a count. A claim is kept only if every cite in it points to a line or commit that was in the evidence pack:

$$
\text{Verified rate} = \frac{\text{claims kept}}{\text{claims kept} + \text{claims dropped}}
$$

On the requests demo this is $81 / 94 \approx 86\%$.
