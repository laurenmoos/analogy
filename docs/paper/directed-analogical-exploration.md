# What Would a Model Reach For?

## Directed Analogical Exploration in Frozen Language Models

**Working paper — initial draft**

### Abstract

Humans frequently reason about unfamiliar or abstract systems by recruiting familiar
structures from intuitive physics, bodily experience, and recurrent narratives. We speak
of an argument as a path, an obligation as a weight, an organization as an organism, or a
damaged joint as a hinge. Such comparisons do more than decorate an explanation: they
make a set of relations available for inference. This paper asks whether a frozen language
model can use an analogous process to bootstrap a task-specific reasoning space from a
small set of target-domain rules, without fine-tuning and potentially at small model scale.

We propose **directed analogical exploration**: an inference-time procedure that retrieves
or generates multiple source structures, makes their source–target mappings explicit,
uses those structures to propose target-domain transitions, and retains only transitions
that survive an external or rule-based verifier. The proposal joins two views of analogy.
The first treats analogy as an explicit, reportable representation, continuous with human
mental models, conceptual metaphors, and narrative schemas. The second treats analogy as
a model-relative phenomenon: a useful source may be one that is natural in the model's
activation geometry even when it would not be the first metaphor a human would produce.
The question is therefore not only *what analogy would a person use?* but *what would this
particular model reach for?*

We operationalize that question using a separation between retrieval, interpretation, and
validation. Embedding similarity measures target relevance; activation displacement
measures whether a candidate changes the model's computation; conditional surprisal
measures whether the change makes a candidate inference more available; causal patching
tests whether the activation change contributes to that availability; and a formal
verifier determines whether the inference is actually useful. A natural-language
source–mapping–transfer record forms an interpretability bottleneck between latent search
and action. A case study in Lean theorem proving illustrates the framework, but the core
hypothesis is domain-general: a model with a few correct rules may be able to construct a
larger effective reasoning space by repeatedly proposing, translating, testing, and
revising analogies at inference time.

## 1. Introduction

Suppose a person is trying to understand pain in a knee. They may imagine a rusty hinge,
a stretched cable, a swollen cushion, or a track that no longer guides a wheel cleanly.
None is a literal model of the joint. Each foregrounds a different relational system:
rotation under friction, tension under load, compression and cushioning, or constrained
motion along a path. The metaphor changes what the person considers next. A hinge suggests
lubrication and range of motion; a cable suggests tension and attachment; a cushion
suggests pressure and inflammation. The comparison functions as a temporary coordinate
system for thought.

What is the corresponding operation for a language model? Asking the model for a familiar
human metaphor gives one answer, but it may not reveal the structures that are easiest for
that model to recruit. A language model's most productive analogy might reflect statistical
regularities distributed across its representations: a pattern of information routing, a
program transformation, a game state, a proof schema, or a narrative transition. It might
be recognizable to a person once expressed, without being the source a person would have
retrieved unaided.

This paper develops the hypothesis that analogical search can be used as an inference-time
learning mechanism. Given a small rule set for a target domain, a frozen model can search
its existing representational resources for source structures, map those structures onto
the target, and use the mappings to generate candidate consequences. Verification turns
this otherwise unconstrained process into disciplined exploration. Successful consequences
expand the working representation of the target; failed consequences revise or eliminate
the analogy. No parameter update is required.

The claim is deliberately narrower than the claim that analogy alone can learn an entire
domain. Directed analogy does not create ground truth. It changes the distribution of
hypotheses a model considers under sparse supervision. The proposed benefit is therefore
an increase in **effective inference capacity**: more useful transitions become reachable
from the same weights and the same target rules.

Three commitments distinguish this proposal from ordinary analogy prompting:

1. **The source is model-relative.** Candidate analogies are searched and compared using
   the frozen model's own embeddings and activations, rather than chosen only from a human
   library of canonical metaphors.
2. **The mapping is explicit.** Before an analogy may influence action, it must be rendered
   as a human-readable source structure, role mapping, transferred relation, predicted
   target state, and stated limitation.
3. **Utility is externally grounded.** Semantic similarity, activation movement, and
   linguistic plausibility are not accepted as success. Candidate inferences must be
   checked against the target's rules or an independent verifier.

These commitments produce a hybrid system: latent representations provide a search space,
language provides an inspection surface, and symbolic or environmental feedback supplies
correction.

## 2. Analogy as a Human Reasoning Mechanism

### 2.1 Relational structure, not shared appearance

The foundational idea in structure-mapping theory is that an analogy aligns relations and
higher-order relational systems rather than merely matching object attributes. In
Gentner's canonical example, an atom and a solar system need not resemble one another at
the object level; what transfers is an organized system of attraction and revolution.
Systematic, mutually constraining relations support richer candidate inferences than an
isolated resemblance does [Gentner, 1983].

This distinction is essential for abstract reasoning. If analogical search merely retrieves
nearby words, examples, or topics, it will mostly reproduce surface associations. A useful
reasoning analogy instead preserves roles, dependencies, transformations, and constraints.
The source provides a structured hypothesis about how the target can change.

Work on analogical problem solving also shows that having a relevant source in memory is
not enough. In the classic radiation-problem studies, a structurally relevant story often
helped only when participants were prompted to use it [Gick and Holyoak, 1980]. Subsequent
work showed that comparing examples can induce a more portable schema [Gick and Holyoak,
1983]. Analogy therefore contains at least three problems: retrieving a source, aligning it
with the target, and transferring an inference. Directed analogical exploration addresses
all three rather than assuming that a single prompted comparison will be retrieved and
used correctly.

The retrieval problem is particularly revealing. The MAC/FAC model distinguishes a cheap,
broad first pass from a more expensive structural comparison. Human remindings are often
driven by surface similarity even when structural similarity governs careful judgment
[Forbus, Gentner, and Law, 1995]. The proposed model procedure inherits this two-stage
logic: use inexpensive model-relative signals to call several candidates, then subject a
small slate to explicit structural mapping and causal measurement.

### 2.2 Naive physics, embodiment, and common narratives

Human abstract reasoning does not begin from an empty set of formal primitives. People
bring extensive expectations about containment, support, force, path, balance, resistance,
and motion. These expectations are useful precisely because they are cheap and readily
available, although research on intuitive physics also demonstrates that they can be
systematically wrong [McCloskey, Washburn, and Felch, 1983]. A naive source model is thus a
generator of candidate inferences, not a guarantee of truth.

Conceptual metaphor theory makes a stronger proposal: familiar physical and social
structures partly organize abstract concepts themselves. Time can be traversed as a path;
an argument can be attacked or defended; quantities can rise and fall [Lakoff and Johnson,
1980]. On this view metaphor is not merely retrospective language attached to a completed
thought. It participates in the representation on which reasoning operates.

Narrative schemas provide another source of portable structure. Scripts and plans encode
typical sequences, roles, goals, interruptions, and repairs [Schank and Abelson, 1977]. A
story such as *an agent lacks a resource, obtains it from an available provider, and then
continues* can organize reasoning in domains far removed from everyday action. A proof
step that supplies the antecedent required by an implication can be cast in exactly this
form. The value of the story lies not in anthropomorphic wording but in its transition
structure.

Naive physics and narrative are attractive source domains because they compress relational
regularities acquired across many experiences. Yet they also impose human-specific priors.
A model trained on code, mathematics, dialogue, and prose may have other highly accessible
sources. Directed analogical exploration generalizes the cognitive insight while relaxing
the assumption that the source library must be the human one.

### 2.3 Explicit mental models and conscious access

Mental-model theory proposes that people reason by constructing representations of the
possibilities compatible with premises. Conclusions are easy when one model suffices and
harder when several alternatives must be represented; errors often reflect a failure to
construct counterexamples or exhaust the relevant possibilities [Johnson-Laird, 2001].
This view connects naturally to proof search. A proof state is not only a string of symbols
but a constrained space of possible continuations, and a countermodel is a concrete reason
to abandon one continuation.

The relationship to consciousness should be stated carefully. The present proposal neither
requires nor implies that a language model is conscious. Consciousness enters as a
functional contrast in the human case. Some cognitive contents become explicit,
reportable, and globally available for flexible control, while much processing remains
implicit. Global-workspace accounts associate conscious access with the widespread
availability of a representation to otherwise specialized processes [Dehaene and Changeux,
2011; Mashour et al., 2020]. An explicit metaphor or mental model can play this coordinating
role for a human: it becomes an object that can be inspected, revised, communicated, and
used to organize subsequent steps.

Our machine analogue is architectural rather than phenomenological. A latent association
becomes **workspace-like** when it is externalized into a persistent representation that
multiple components can use: candidate generation, prediction, verification, memory, and
revision. The explicit analogy scratchpad is therefore not evidence of machine experience.
It is a control interface that gives otherwise transient model activity some of the
functional properties of an explicit mental representation.

This distinction also prevents a common interpretability error. A verbalized analogy need
not be a faithful transcript of the computation that produced it. It is better treated as
a constrained, testable model of that computation. Its credibility increases when the
stated mapping predicts target transitions, changes the likelihood of those transitions,
and survives causal intervention and external verification.

## 3. From Human Metaphors to Model-Native Analogies

### 3.1 What would the model reach for?

For a human, asking “what does this knee feel like?” may retrieve a hinge because joints
and hinges share an obvious embodied relation. For a language model, retrieval has no
single introspectively available source. Candidate sources may be reflected in at least
four observable spaces:

- the language distribution, through source descriptions or continuations the model finds
  probable;
- embedding space, through proximity between target states and source descriptions;
- activation space, through internal trajectories induced by a source–target mapping; and
- action space, through the target transitions made more likely by the candidate.

No one space should be identified with “the model's real analogy.” Instead, we define a
model-native analogy operationally: it is a human-interpretable relational source whose
presence produces a reproducible and causally relevant change in the model's target-domain
inferences.

This definition is relational and interventionist. A source is not model-native merely
because the model can fluently describe it, nor because its embedding is close to the
target. It must alter what the model can do. Conversely, the source need not correspond to
a single localized feature. It may be a distributed direction or trajectory that becomes
legible only through its effect on candidate actions.

### 3.2 Evidence for latent task and analogical structure

Language models can solve a range of explicit analogy tasks, including verbal analogies,
letter-string transformations, story analogies, and text versions of matrix problems
[Webb, Holyoak, and Lu, 2023]. This establishes behavioral competence, but it does not show
how analogical structure is represented or whether a retrieved source causes a useful
inference.

Prompting methods have begun to use analogies procedurally. Thought Propagation asks a
model to construct and solve analogous problems before returning to the target [Yu, He,
and Ying, 2024]. Analogical prompting similarly asks models to generate relevant exemplars
for their own reasoning [Yasunaga et al., 2024]. These methods demonstrate that inference-
time self-generated analogs can improve performance. They generally select and evaluate
analogs through their textual products, however, leaving open whether a candidate matches
the model's internal computation or merely supplies more tokens and additional attempts.

Mechanistic work makes a stronger intervention possible. Function vectors provide evidence
that some in-context tasks are represented by compact, causally effective activation
patterns that can be transported across contexts [Todd et al., 2024]. Representation-
engineering methods likewise treat population-level activation patterns as objects that
can be measured and manipulated [Zou et al., 2023]. These findings do not establish that
analogies themselves are vectors, nor that a single direction exhausts a model's reasoning.
They do justify testing whether an explicitly stated analogy induces a stable displacement
that changes the probability of a mapped target action.

### 3.3 The interpretability bottleneck

Pure latent search creates a problem of epistemic access. A system might find an activation
intervention that improves performance without yielding any account a person can evaluate.
Pure verbal analogy creates the inverse problem: a persuasive explanation may be causally
irrelevant to the model's action.

Directed analogical exploration connects the two through an interpretability bottleneck.
Each candidate must expose:

1. a concrete source situation;
2. the relevant source roles and relations;
3. an explicit mapping from source roles to target roles;
4. the transformation proposed by the analogy;
5. the predicted target state or consequence;
6. one or more candidate actions; and
7. a limitation or condition under which the mapping should be abandoned.

The textual record is then paired with measurements of the computation it induces. A
candidate is interpretable not because its prose sounds intuitive, but because its declared
mapping, measured effect, predicted consequence, and verified result can be compared.

## 4. Directed Analogical Exploration

Let a target problem be described by a current state \(s\), a small rule set \(R\), and a
set of available actions \(Y\). The model parameters \(\theta\) remain fixed. The procedure
maintains an external working theory \(W_t\), initially containing only the supplied rules
and verified transitions.

### 4.1 Candidate generation

At an impasse—or proactively when uncertainty is high—the system generates a diverse slate
of source structures \(A_1,\ldots,A_K\). Diversity can be encouraged with domain cues,
sampling, embedding-space dispersion, or retrieval from different representational
neighborhoods. Each candidate is rendered through the interpretability bottleneck and
proposes a bounded action slate.

This stage is exploratory. Its purpose is to make qualitatively different inference
coordinates available. Consequently, novelty and activation displacement are reasonable
generation diagnostics. They are not yet evidence of usefulness.

### 4.2 Structural alignment

For candidate \(A_i\), the system constructs a partial mapping

\[
M_i : \text{roles}(A_i) \rightarrow \text{roles}(s,R).
\]

The mapping should preserve a connected relational system rather than a bag of attributes.
It licenses candidate inferences by substituting target roles into the source's relational
transformation. Mappings are partial because a productive analogy need not make every
source feature correspond, and forcing complete correspondence encourages hallucinated
structure.

### 4.3 Four distinct pre-verification signals

We propose keeping four signals separate before combining them for selection.

**Target relevance.** A frozen embedding model estimates whether the candidate's
domain-neutral relational summary addresses the current proof or problem need:

\[
Q_{\mathrm{rel}}(A_i,s) = \cos(e(A_i), e(s)).
\]

This is a retrieval measure. It does not show that the candidate changes reasoning.

**Reasoning displacement.** Teacher-force the same candidate action \(y\) with and without
the analogy and compare pooled activations:

\[
\delta_i(y) = Z_\theta(s,A_i,y)-Z_\theta(s,y).
\]

A covariance-normalized distance

\[
D_i(y)=\delta_i(y)^\top(\Sigma_0+\epsilon I)^{-1}\delta_i(y)
\]

measures movement relative to neutral variation. This is a diversification measure, not a
progress measure.

**Conditional surprisal change.** For a proposed action \(y\), define

\[
\Delta I_i(y)
= -\log p_\theta(y\mid s,A_i)+\log p_\theta(y\mid s).
\]

Positive \(\Delta I_i\) means that the analogy reduces the action's surprisal—it makes the
action more available than it was in the neutral context. Crucially, high surprisal alone
is not desirable. Exploration may produce surprising candidates, but selection should
prefer a candidate that makes an ultimately valid action less surprising. Otherwise the
method rewards confusion.

**Predecessor explanatory value.** When a preceding verified state \(s^-\) exists, compare
the action probability with and without that transition history:

\[
E_i(y) = \log p_\theta(y\mid s^-,s,A_i)
       - \log p_\theta(y\mid s,A_i).
\]

This tests whether the analogy coheres with the observed trajectory rather than merely
matching the current state in isolation.

### 4.4 Causal selection

Correlation between an analogy and an action is insufficient. We therefore patch a bounded
version of \(\delta_i\) into a neutral run and measure

\[
C_i(y)=\log p_\theta(y\mid s,\operatorname{patch}(\delta_i))
       -\log p_\theta(y\mid s,\operatorname{patch}(0)).
\]

If the candidate produces a large descriptive displacement but negligible or adverse
causal effect, it should not be interpreted as a useful reasoning direction. An initial
selector can combine cross-fitted, standardized relevance, displacement, causal likelihood
gain, and predecessor value, while requiring a strictly positive causal threshold. More
conservative variants can use the measurements only to prune candidates and leave final
selection to the verifier.

### 4.5 Verification and revision

The selected analogy proposes a target action and a predicted successor state. An
independent mechanism evaluates both:

\[
V_R(s,y) \in \{\text{reject},\text{accept-progress},\text{complete}\}.
\]

In theorem proving, \(V_R\) is a proof assistant plus, where appropriate, a semantic
countermodel checker. In planning it may be a simulator; in program synthesis, a test
suite and type checker; in a scientific microdomain, a set of supplied equations and
measurements.

Accepted transitions are added to \(W_t\). A locally unhelpful action can prompt revision
of the analogy's steps while retaining the mapping. A counterexample to the transferred
structure should trigger a stronger response: abandon the source mapping and retrieve a
different domain. In this way analogy functions as a defeasible working theory.

### 4.6 The bootstrap loop

The complete procedure is:

\[
\text{few rules}
\rightarrow \text{source search}
\rightarrow \text{explicit mapping}
\rightarrow \text{candidate inference}
\rightarrow \text{verification}
\rightarrow \text{expanded working theory}.
\]

The loop is a form of inference-time learning because verified transitions and rejected
mappings change the model's effective context and search policy without changing its
weights. The working theory is local, inspectable, and disposable. It is closer to building
a temporary mental model than to acquiring a parameterized skill.

## 5. Case Study: Analogy-Guided Lean Proof Search

The accompanying system instantiates this framework in a deliberately unforgiving target
domain: Lean theorem proving. The base model is frozen. Conventional tactic generation owns
the search frontier, budgets, backtracking, and ordinary progress. Analogy is invoked as a
bounded intervention, especially when conventional search becomes stuck. Lean is the sole
judge of tactic acceptance and proof completion.

This setting cleanly separates three things that are often conflated:

- a change in the model's internal computation;
- novelty among reached proof states; and
- actual proof utility.

The current evaluation design measures target relevance with frozen embeddings, reasoning
diversification with whitened activation displacement, causal utility with patched tactic
log-likelihood, and trajectory coherence with predecessor explanatory value. Candidate
selection occurs before Lean evaluation. The chosen candidate then exposes its stored,
ordered tactic plan to Lean. Equal-tactic and equal-total-compute comparisons are reported
separately because analogical search consumes additional inference.

The persistent scratchpad gives the source domain, mapping, and numbered story an explicit
lifecycle. Ordinary progress cannot silently rewrite the analogy. A stuck state may revise
the story or replace the domain. A semantic countermodel forces domain replacement and
records the abandoned source. This makes the system's analogical commitments auditable and
allows failure to have a structured consequence.

One initial single-seed smoke run with an 8-billion-parameter quantized model produced a
proof completion in the activation-selected condition where the matched neutral plan did
not complete. The other evaluated cells did not yield selected candidates under the
configured abstention rule. This is engineering evidence that the pipeline can produce a
causally measured, verifier-accepted success; it is not statistical evidence for the
research hypothesis. Multi-seed, multi-problem evaluation remains necessary.

Lean is useful here not because theorem proving exhausts abstract reasoning, but because it
prevents a fluent analogy from grading itself. A model may tell a compelling story about
resources, containers, locks, paths, or information flow. Only a kernel-checked transition
can establish that the story led somewhere valid.

## 6. Experimental Program

### 6.1 Primary research questions

1. Can directed analogical exploration improve verified task performance for a frozen
   model relative to direct generation, ordinary search, and text-only analogy prompting?
2. Does the advantage increase as target-domain supervision becomes sparser?
3. Can a small model recover some of the effective reasoning capacity of a larger model by
   spending additional inference-time computation on analogical search?
4. Are model-native candidates more useful than human-authored canonical metaphors?
5. Does forcing an interpretable mapping preserve most of the benefit of unconstrained
   latent steering, or does it impose a substantial performance cost?
6. Do the pre-verification signals predict verified utility out of distribution?

### 6.2 Conditions

A decisive experiment should compare at least the following matched conditions:

- **Neutral:** direct reasoning with the target rules and ordinary search.
- **Human-source analogy:** a source selected from a preregistered library of familiar
  physical and narrative schemas.
- **Text-self-generated analogy:** the model proposes and uses an analogy in language,
  without activation-based selection.
- **Model-native directed analogy:** candidates are generated broadly and selected with
  model-relative representational and causal measurements.
- **Shuffled mapping control:** the same textual and computational overhead is retained but
  source roles are assigned to incompatible target roles.
- **Latent-only intervention:** a candidate direction is applied without requiring a
  human-readable mapping, providing an upper-bound comparison for the interpretability
  bottleneck.

Comparisons must be made both at equal action/verifier budget and at equal total model
compute. Otherwise an analogy condition may win simply by making more model calls.

### 6.3 Task families

The initial theorem-proving study should be followed by domains that vary in their verifier
and in the kind of abstraction required:

- intuitionistic propositional proofs with out-of-distribution compositions;
- small algorithmic or program-synthesis tasks with unit-test verification;
- planning problems with deterministic simulators;
- causal micro-worlds whose dynamics are specified by a few rules; and
- scientific reasoning tasks in which supplied equations constrain permissible transfer.

For each family, target rules should be manipulated independently from pretrained domain
familiarity. Synthetic symbol substitutions and newly generated micro-worlds can reduce the
chance that the model is retrieving a memorized target solution rather than bootstrapping a
reasoning space.

### 6.4 Scale and adaptation tests

Evaluate multiple frozen model sizes under the same protocol. The strongest version of the
hypothesis predicts an interaction: directed exploration should help small models more than
large models when the small model already contains the relevant source structure but fails
to retrieve or coordinate it. It will not help when the source relations themselves are
absent from the model.

The no-fine-tuning claim should be tested directly against lightweight adaptation. Compare
direct prompting, directed analogical exploration, low-rank fine-tuning, and their
combination. Analogy may provide fast, reversible task adaptation; fine-tuning may provide
more efficient repeated execution once a domain is encountered many times.

### 6.5 Measurements and preregistered predictions

Primary outcomes should be externally verified completion and verified progress per unit of
total compute. Secondary outcomes include calibration of predicted successor states,
abstention quality, number of abandoned mappings, and transfer to new tasks governed by the
same relational schema.

The following predictions are especially diagnostic:

- embedding relevance alone will retrieve plausible but often inert candidates;
- activation displacement alone will favor candidates that are different, including
  pathological ones;
- conditional likelihood gain without verification will favor confident errors;
- causal selection plus verification will outperform any one of these signals alone;
- shuffled role mappings will preserve topic and prompt length but lose most of the gain;
- useful model-native analogies will sometimes differ systematically from the analogies
  preferred or rated most intuitive by humans; and
- explicit mappings will enable better error diagnosis and safer abandonment than opaque
  latent interventions, even if the latter sometimes achieve higher raw performance.

## 7. Relation to Existing Work

The proposal is closest to work that uses self-generated analogous problems to improve
language-model reasoning. It differs by making retrieval model-relative, separating
descriptive activation change from causal action change, and requiring external validation.
It is also related to representation engineering, but its objective is not to impose a
predefined trait or concept direction. Instead, it searches for temporary relational
structures that help a model act under a new rule system.

Relative to classical cognitive accounts, directed analogical exploration turns the human
retrieval problem into an instrumented model-selection problem. Structure mapping supplies
the unit of transfer; MAC/FAC motivates broad retrieval followed by expensive comparison;
mental-model theory motivates explicit alternatives and counterexamples; conceptual
metaphor and scripts identify why familiar source systems can organize abstract thought.
The machine setting adds something unavailable in human experiments: the ability to
teacher-force identical actions, measure internal displacement, intervene on that
displacement, and compare the result with a formal verifier.

The broader contribution is therefore not another claim that models “reason like humans.”
It is a framework for comparing two ways that a finite reasoner can reuse prior structure.
Humans often recruit embodied and narrative schemas. Language models may recruit structures
distributed through their training-induced geometry. Both can make those structures
explicit; neither should be trusted without testing the inferences they license.

## 8. Limitations and Failure Modes

First, verbalized analogies can be post hoc rationalizations. The interpretability
bottleneck mitigates but does not eliminate this risk. Causal interventions test whether
the measured activation change affects an action, not whether the prose uniquely describes
the full computation.

Second, activation directions are context- and architecture-dependent. A displacement
measured at one layer and token boundary is only a local proxy for a distributed reasoning
trajectory. Whitening, cross-fitting, and held-out evaluation reduce statistical artifacts
but do not turn a proxy into a complete mechanism.

Third, external verification is easiest in formal domains. In open-ended scientific or
social reasoning, the verifier may itself be incomplete or contested. The system should
then retain uncertainty and provenance rather than promote a surviving analogy to fact.

Fourth, model-native retrieval may surface culturally biased, misleading, or inaccessible
sources. Human interpretability is not equivalent to harmlessness. Candidate libraries,
abandonment rules, and audits remain necessary.

Fifth, additional inference-time search may erase the practical advantages of a small
model. Equal-compute evaluation is therefore central, not optional. A result that improves
accuracy only with orders of magnitude more computation would support a scientific claim
about latent capacity but a weaker engineering claim about efficiency.

Finally, analogy may be most useful in a middle regime. With too little target structure,
there is no basis for rejecting seductive transfers. With a complete formal theory,
ordinary deduction may already be sufficient. The method is aimed at domains in which a
small number of reliable constraints can discipline a large space of possible inferences.

## 9. Conclusion

Directed analogical exploration treats analogy as neither ornament nor oracle. It is a
method for proposing temporary inference coordinates. A source domain makes relations
available; an explicit mapping says what is being transferred; a model-relative signal
estimates whether the source changes the computation; and a verifier decides whether the
result belongs in the target's growing working theory.

The resulting research question is concrete: when a frozen model knows only a few rules of
a new domain, can it use its existing representations to construct a better local reasoner
at inference time? The human comparison supplies a productive starting point, because
people routinely recruit naive physics, bodily schemas, and stories to make abstractions
tractable. But the goal is not to force models to use our favorite metaphors. It is to
discover what a particular model can reach for, make that structure legible, and test
whether it genuinely carries reasoning across domains.

## References

- Dehaene, S., and Changeux, J.-P. (2011). Experimental and theoretical approaches
  to conscious processing. *Neuron, 70*(2), 200–227.
  https://doi.org/10.1016/j.neuron.2011.03.018
- Forbus, K. D., Gentner, D., and Law, K. (1995). MAC/FAC: A model of
  similarity-based retrieval. *Cognitive Science, 19*(2), 141–205.
  https://doi.org/10.1207/s15516709cog1902_1
- Gentner, D. (1983). Structure-mapping: A theoretical framework for analogy.
  *Cognitive Science, 7*(2), 155–170.
  https://doi.org/10.1207/s15516709cog0702_3
- Gick, M. L., and Holyoak, K. J. (1980). Analogical problem solving.
  *Cognitive Psychology, 12*(3), 306–355.
  https://doi.org/10.1016/0010-0285(80)90013-4
- Gick, M. L., and Holyoak, K. J. (1983). Schema induction and analogical
  transfer. *Cognitive Psychology, 15*(1), 1–38.
  https://doi.org/10.1016/0010-0285(83)90002-6
- Johnson-Laird, P. N. (2001). Mental models and deduction. *Trends in Cognitive
  Sciences, 5*(10), 434–442. https://doi.org/10.1016/S1364-6613(00)01751-4
- Lakoff, G., and Johnson, M. (1980). *Metaphors We Live By*. University of
  Chicago Press. https://doi.org/10.7208/chicago/9780226470993.001.0001
- Mashour, G. A., Roelfsema, P., Changeux, J.-P., and Dehaene, S. (2020).
  Conscious processing and the global neuronal workspace hypothesis. *Neuron,
  105*(5), 776–798. https://doi.org/10.1016/j.neuron.2020.01.026
- McCloskey, M., Washburn, A., and Felch, L. (1983). Intuitive physics: The
  straight-down belief and its origin. *Journal of Experimental Psychology:
  Learning, Memory, and Cognition, 9*(4), 636–649.
  https://doi.org/10.1037/0278-7393.9.4.636
- Schank, R. C., and Abelson, R. P. (1977). *Scripts, Plans, Goals, and
  Understanding*. Lawrence Erlbaum Associates.
- Todd, E., Li, M., Sharma, A. S., Mueller, A., Wallace, B. C., and Bau, D.
  (2024). Function vectors in large language models. *International Conference
  on Learning Representations*. https://openreview.net/forum?id=AwyxtyMwaG
- Webb, T., Holyoak, K. J., and Lu, H. (2023). Emergent analogical reasoning in
  large language models. *Nature Human Behaviour, 7*, 1526–1541.
  https://doi.org/10.1038/s41562-023-01659-w
- Yasunaga, M., Chen, X., Li, Y., Pasupat, P., Leskovec, J., Liang, P., Chi,
  E. H., and Zhou, D. (2024). Large language models as analogical reasoners.
  *International Conference on Learning Representations*.
  https://arxiv.org/abs/2310.01714
- Yu, J., He, R., and Ying, R. (2024). Thought Propagation: An analogical
  approach to complex reasoning with large language models. *International
  Conference on Learning Representations*.
  https://openreview.net/forum?id=SBoRhRCzM3
- Zou, A., Phan, L., Chen, S., et al. (2023). Representation engineering: A
  top-down approach to AI transparency. arXiv:2310.01405.
  https://arxiv.org/abs/2310.01405
