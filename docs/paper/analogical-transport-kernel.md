# Verifier-Guided Analogical Transport in Language Models

## Claim

On this account, a model is creative when structure learned in one medium changes its search in a remote domain and produces a novel, verifiably useful result.

Imagine a model that uses a painting or a ballet to help solve a proof. It identifies a structural pattern, such as symmetry, role exchange, or counterbalance, and asks whether that pattern suggests a useful proof move. Lean tests the move. The artwork matters only if it improves the search.

The ambition has something of Hesse's *Glass Bead Game* in it: a disciplined play of correspondences across mathematics, music, logic, philosophy, and art ([Hesse, 1943/1969](https://search.worldcat.org/title/33450)). Here, however, the correspondences are tested. A connection earns its place only when it guides the model toward a verified result.

Research on human reasoning gives this proposal a basis. Grounded-cognition work argues that conceptual thought reuses perceptual, motor, and situated simulations ([Barsalou, 2008](https://doi.org/10.1146/annurev.psych.59.103006.093639)). Structure-mapping theory describes analogy as the preservation of relations when the objects change ([Gentner, 1983](https://doi.org/10.1207/s15516709cog0702_3)). In physics education, bridging analogies connect an intuitive case to a difficult target through intermediate steps ([Clement, Brown, & Zietsman, 1989](https://doi.org/10.1080/0950069890110507); [Brown & Clement, 1989](https://doi.org/10.1007/BF00118013); [Clement, 1993](https://doi.org/10.1002/tea.3660301007)). These traditions treat abstraction as a path from familiar structure to unfamiliar structure.

We argue that language models can learn a comparable strategy in their own representational medium. The model compares a familiar state machine with a new one, maps their transitions, reuses an operation, and tests the result. Compiler, test, or Lean feedback supplies the training signal. An early system can describe candidate connections in readable language and rank them with activation measurements. Verified successes can then teach a smaller policy model to construct analogies directly. Its objective is to reduce the Gromov–Wasserstein distance between source and target state machines, one mapping decision at a time.

## Mechanism

Represent a verified proof transition as

\[
(s_t,a_t,s_{t+1}),
\]

where \(s_t\) and \(s_{t+1}\) are proof states and \(a_t\) is the tactic that moves between them. A proof trace is therefore a state machine: proof states are nodes and tactics are directed edges. Comparing only \(h(s_{t+1})-h(s_t)\) assumes that analogous transitions follow parallel vectors. Superposition and feature manifolds suggest that the same computation may instead appear as a similar pattern of movement through differently arranged activations.

Take two small state machines: a source proof \(X\) and the current target proof \(Y\):

\[
X=\{x_i\}_{i=1}^{m}, \qquad Y=\{y_j\}_{j=1}^{n}.
\]

Each contains embedded proof states connected by tactics. Find a mapping \(\Pi\) between their nodes and edges that preserves how the states relate and how the transitions change them:

\[
\operatorname{GW}(X,Y)=
\min_{\Pi}
\sum_{i,k,j,l}
\left|d_X(x_i,x_k)-d_Y(y_j,y_l)\right|^2
\Pi_{ij}\Pi_{kl}.
\]

This simplified Gromov–Wasserstein objective asks whether the two state machines have the same shape even when they occupy different coordinates. The distance should reflect what the model does: which tactics become likely, which next states it predicts, and how an activation intervention changes those predictions.

Score each proposed analogy \(A\) by

\[
S(A)=
-\alpha\,\widehat{\operatorname{GW}}(A,s)
+\beta\,C(A,s)
+\gamma\,P(A,s)
+\eta\,U(A).
\]

Here \(C\) measures whether the mapping makes the corresponding target tactic more likely, \(P\) estimates progress, and \(U\) gives some search time to new mappings. Lean then tests the target tactic. A transition that produces the predicted proof state supports the mapping; other results send the search elsewhere.

### Learned analogy policy

The final system replaces repeated free-form prompting with a policy \(\pi_\theta\) over a small set of analogy-building actions:

\[
\textsc{OpenSource},\quad
\textsc{Bind},\quad
\textsc{Extend},\quad
\textsc{Revise},\quad
\textsc{Test},\quad
\textsc{Commit},\quad
\textsc{Abandon}.
\]

Each action may point to a domain, state, or transition, but the controller learns only these seven kinds of move.

The actions form a grammar rather than a flat list of complete analogies:

\[
\text{Analogy}
\rightarrow
\textsc{OpenSource}
(\textsc{Bind}\mid\textsc{Extend}\mid\textsc{Revise})^{*}
\textsc{Test}
(\textsc{Commit}\mid\textsc{Abandon}).
\]

At step \(t\), the policy edits a partial correspondence \(\Pi_t\). Training rewards reductions in mismatch together with verified proof progress:

\[
r_t=
\mathcal{L}_{\mathrm{GW}}(\Pi_t;X,Y)
-\mathcal{L}_{\mathrm{GW}}(\Pi_{t+1};X,Y)
+\lambda R_{\mathrm{Lean}}
-\kappa C_{\mathrm{compute}}.
\]

This gives the search two scales. The outer policy explores different source domains; the inner policy exploits one promising source by progressively binding states, extending matched transitions, and repairing the mapping. A temperature parameter \(\tau\) controls how associative the search is:

\[
\pi_\theta(a\mid \Pi_t,Y,\tau)
\propto
\exp\!\left(Q_\theta(a\mid \Pi_t,Y)/\tau\right).
\]

High temperature spreads probability across remote domains and competing interpretations. Low temperature concentrates computation on refining the best current mapping. Temperature can fall as structural fit and verifier evidence accumulate, then rise again when the mapping stops improving. The policy therefore learns both a grammar for constructing analogies and a schedule for moving between association and precision.

\[
\text{propose a source state machine}
\rightarrow \text{map its transitions to the target}
\rightarrow \text{choose the best mapping from activations}
\rightarrow \text{apply the corresponding tactic}
\rightarrow \text{verify}
\rightarrow \text{update}.
\]

Prompting suggests mappings; activation measurements and Lean decide whether they work.

## Minimal experiment

Begin when the ordinary solver runs out of tactics. Ask the model for several source proof traces and a correspondence between each source state machine and the current proof. Each correspondence says which states play the same role and which source transition suggests the next target tactic. Compare four conditions:

1. direct tactic generation;
2. one model-generated comparison followed by a tactic;
3. several comparisons, with activation measurements choosing one, followed by a tactic;
4. the same comparison with its state correspondence scrambled.

Choose the mapping before submitting any tactic to Lean. Measure verified progress and completed proofs under equal compute and Lean-call budgets. Then test whether the activation score predicts success, whether activation patching changes tactic preference as predicted, and whether the mapping survives changes in wording and subject matter.

The strongest evaluation trains and tests on different kinds of transition patterns. Evidence for analogy means reusing a transition across different vocabulary and content, preferring a source with the same state-machine structure over one with similar words, and outperforming both a single prompted comparison and scrambled mappings.

## Position

Barsalou's account of grounded cognition treats conceptual processing as simulation in perceptual, motor, bodily, and situated systems. Gentner's structure-mapping theory explains how relations from a familiar source can organize a new domain. Gentner and Gentner (1983) found that water-flow and moving-crowd models of electricity supported systematically different inferences about circuits. Clement (1993) reported larger pre/post gains for physics lessons that used intuitive anchors and intermediate bridging analogies. Our state-machine formulation turns these cognitive results into a model-level operation whose proposed bridges, internal effects, and verified consequences can be measured.

Yasunaga et al. have models generate relevant solved examples before answering. Zhou et al. train models to reuse successful symbolic solutions on harder cases. We ask whether activation measurements can choose among proposed source-to-target state-machine mappings and improve that reuse.

Elhage et al. describe features sharing the same activation dimensions through superposition. Gurnee et al. show that a model may represent a variable as a manifold and compute by moving along it. We therefore compare patterns of state change instead of looking for one fixed analogy vector.

Gromov–Wasserstein transport compares the shapes of two state machines without requiring their coordinates to match. Minegishi et al. likewise separate finding a structural match from applying it. Function vectors show that activation directions can control tasks, while Opiełka et al. show that causal control and stability across formats can differ; the experiment measures both.

The policy also has a direct cognitive-computational precedent in Copycat. Mitchell and Hofstadter (1990) used many small local actions to build and revise partial analogies, with a computational temperature controlling randomness as perceptual organization emerged. Our proposal turns that idea into a trainable policy over model activations, with Gromov–Wasserstein fit and formal verification supplying the learning signal.

RLEF shows that code models can learn from execution feedback across attempts. DeepSeek-Prover-V1.5 combines Lean feedback, reinforcement learning, and proof search. Our contribution is to make the correspondence between two state machines the object that the system proposes, scores, tests, and revises.

## References

- Barsalou, L. W. (2008). [Grounded cognition](https://doi.org/10.1146/annurev.psych.59.103006.093639). *Annual Review of Psychology, 59*, 617–645.
- Brown, D. E., & Clement, J. (1989). [Overcoming misconceptions via analogical reasoning: Abstract transfer versus explanatory model construction](https://doi.org/10.1007/BF00118013). *Instructional Science, 18*, 237–261.
- Clement, J., Brown, D. E., & Zietsman, A. (1989). [Not all preconceptions are misconceptions: Finding “anchoring conceptions” for grounding instruction on students' intuitions](https://doi.org/10.1080/0950069890110507). *International Journal of Science Education, 11*(5), 554–565.
- Clement, J. (1993). [Using bridging analogies and anchoring intuitions to deal with students' preconceptions in physics](https://doi.org/10.1002/tea.3660301007). *Journal of Research in Science Teaching, 30*(10), 1241–1257.
- Elhage, N., et al. (2022). [Toy models of superposition](https://transformer-circuits.pub/2022/toy_model/index.html). *Transformer Circuits Thread*.
- Gehring, J., Zheng, K., Copet, J., Mella, V., Carbonneaux, Q., Cohen, T., & Synnaeve, G. (2024). [RLEF: Grounding code LLMs in execution feedback with reinforcement learning](https://arxiv.org/abs/2410.02089). *arXiv:2410.02089*.
- Gentner, D. (1983). [Structure-mapping: A theoretical framework for analogy](https://doi.org/10.1207/s15516709cog0702_3). *Cognitive Science, 7*(2), 155–170.
- Gentner, D., & Gentner, D. R. (1983). [Flowing waters or teeming crowds: Mental models of electricity](https://groups.psych.northwestern.edu/gentner/papers/GentnerGentner83.pdf). In D. Gentner & A. L. Stevens (Eds.), *Mental Models* (pp. 99–129). Lawrence Erlbaum Associates.
- Gurnee, W., Ameisen, E., Kauvar, I., Tarng, J., Pearce, A., Olah, C., & Batson, J. (2025). [When models manipulate manifolds: The geometry of a counting task](https://transformer-circuits.pub/2025/linebreaks/index.html). *Transformer Circuits Thread*.
- Hesse, H. (1969). [*The Glass Bead Game (Magister Ludi)*](https://search.worldcat.org/title/33450) (R. Winston & C. Winston, Trans.). Holt, Rinehart and Winston. (Original work published 1943).
- Minegishi, G., Feng, J., Furuta, H., Kojima, T., Iwasawa, Y., & Matsuo, Y. (2026). [Emergent analogical reasoning in Transformers](https://arxiv.org/abs/2602.01992). *Proceedings of the 43rd International Conference on Machine Learning*.
- Mitchell, M., & Hofstadter, D. R. (1990). [The emergence of understanding in a computer model of concepts and analogy-making](https://doi.org/10.1016/0167-2789(90)90086-5). *Physica D: Nonlinear Phenomena, 42*(1–3), 322–334.
- Opiełka, G., Rosenbusch, H., & Stevenson, C. E. (2026). [Causality ≠ invariance: Function and concept vectors in LLMs](https://arxiv.org/abs/2602.22424). *Proceedings of the International Conference on Learning Representations*.
- Peyré, G., Cuturi, M., & Solomon, J. (2016). [Gromov–Wasserstein averaging of kernel and distance matrices](https://proceedings.mlr.press/v48/peyre16.html). *Proceedings of the 33rd International Conference on Machine Learning*, PMLR 48, 2664–2672.
- Todd, E., Li, M. L., Sharma, A. S., Mueller, A., Wallace, B. C., & Bau, D. (2024). [Function vectors in large language models](https://openreview.net/forum?id=AwyxtyMwaG). *Proceedings of the International Conference on Learning Representations*.
- Xin, H., et al. (2025). [DeepSeek-Prover-V1.5: Harnessing proof assistant feedback for reinforcement learning and Monte-Carlo tree search](https://arxiv.org/abs/2408.08152). *Proceedings of the International Conference on Learning Representations*.
- Yasunaga, M., Chen, X., Li, Y., Pasupat, P., Leskovec, J., Liang, P., Chi, E. H., & Zhou, D. (2024). [Large language models as analogical reasoners](https://arxiv.org/abs/2310.01714). *Proceedings of the International Conference on Learning Representations*.
- Zhou, B., Jain, S., Zhang, Y., Ning, Q., Wang, S., Benajiba, Y., & Roth, D. (2025). [Self-supervised analogical learning using language models](https://arxiv.org/abs/2502.00996). *arXiv:2502.00996*.
