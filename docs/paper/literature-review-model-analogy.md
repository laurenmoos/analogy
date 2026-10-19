# Literature Review: Model-Native Analogy, Latent Structure, and Explicit Access

**Working review for _Directed Analogical Exploration in Frozen Language Models_**
**Literature checked through October 18, 2026**

## Scope and organizing question

This review asks a narrower question than whether language models can answer analogy
questions: **can a frozen model deliberately retrieve, construct, and test analogical
structures that expand its effective reasoning space?** The distinction matters. A model
may complete `A:B::C:?` from lexical statistics without constructing a portable relational
representation. Conversely, it may contain a useful relational representation without
retrieving it in the current context or translating it into a correct action.

The review therefore separates five levels of evidence:

1. **Behavioral competence:** the model answers an analogy task correctly.
2. **Robust transfer:** performance survives counterfactual symbols, paraphrase, changed
   answer order, distant domains, and unfamiliar targets.
3. **Inference-time use:** an analogy improves a separate reasoning or planning task.
4. **Internal representation:** model activations encode a relation or task abstraction.
5. **Causal use:** intervening on that representation changes the mapped inference.

The literature is strongest at levels 1 and 3, increasingly active at levels 4 and 5, and
still mixed at level 2. That pattern motivates directed analogical exploration: retrieve
several candidate structures, state their mappings explicitly, measure whether they alter
the model's computation, and let an external verifier decide whether the transfer is valid.

This is a targeted critical review rather than a formal systematic review. It prioritizes
primary publications and recent papers on LLM analogy, mechanistic representation, and
inference-time intervention, with classical cognitive work included only where it defines
the computational problem.

## 1. The cognitive template: retrieval, mapping, and transfer

Modern work on model analogy inherits its basic decomposition from cognitive science.
Structure-mapping theory defines analogy primarily as correspondence between relational
systems, not shared object attributes. A good mapping preserves connected and higher-order
relations, and thereby licenses candidate inferences in the target [Gentner, 1983]. This is
the core reason that analogy is potentially useful for abstract reasoning: the source
supplies an organized transition structure rather than a nearby topic.

Human experiments also establish that possession of a useful source does not guarantee its
use. Participants exposed to a structurally relevant military story often transferred its
solution to a medical radiation problem only after receiving a hint [Gick and Holyoak,
1980]. Comparing multiple sources can induce a more portable schema [Gick and Holyoak,
1983]. The resulting computational problem has at least three stages:

- **retrieval** of a potentially useful source;
- **mapping** of source roles and relations to the target; and
- **transfer** of a source inference into a target action.

MAC/FAC formalized a related two-stage retrieval process: a cheap similarity filter calls a
small set of candidates, after which a more expensive structural matcher evaluates them.
The model explains an important human asymmetry: people value relational structure when
comparing items in working memory, yet spontaneous retrieval is often dominated by surface
similarity [Forbus, Gentner, and Law, 1995]. This asymmetry reappears throughout the LLM
literature. Models can sometimes judge a supplied structural correspondence, but retrieving
the right distant source from a large or distracting context is substantially harder.

These theories do not settle whether neural language models perform analogy in the same way
as people. They do give the field useful failure labels. A wrong answer may be a retrieval
failure, a role-alignment failure, a transfer failure, or a failure of an auxiliary capacity
such as counting. Benchmarks that collapse these stages into accuracy make mechanistic
claims difficult to evaluate.

## 2. Behavioral evidence: impressive competence, limited robustness

### 2.1 The positive case

Webb, Holyoak, and Lu's influential 2023 study compared GPT-3 with people on text versions
of matrix reasoning, letter-string transformations, verbal analogies, and story analogies.
The model matched or exceeded human aggregate performance in several settings, supporting
the claim that broad next-token training can yield substantial zero-shot analogical
behavior [Webb, Holyoak, and Lu, 2023]. The importance of this result is architectural: the
model was not built around an explicit symbolic analogy engine.

The result should nevertheless be read as evidence of task competence, not by itself as
evidence of a general and invariant analogy mechanism. Text-based tasks may overlap with
training distributions; aggregate accuracy can conceal qualitatively different error
patterns; and common formats may allow retrieval of familiar procedures.

Webb and colleagues later responded to counterfactual evaluations using shuffled alphabets.
They showed that GPT-4 augmented with code execution could perform roughly at human level
on variants that defeated the unaided model, arguing that some failures reflected precise
indexing or counting demands rather than analogy itself [Webb, Holyoak, and Lu, 2025]. This
reply is theoretically important because re-representation is itself a plausible part of
analogical problem solving. It also complicates the interpretation: the augmented system
demonstrates that the overall architecture can solve the task, but not that the base model's
unaided analogical transfer is robust.

### 2.2 Counterfactual and perturbation evidence

Lewis and Mitchell provide the clearest robustness challenge. They modify familiar analogy
tasks while preserving their intended abstract rule: alphabets are permuted, letters are
replaced with unfamiliar symbols, the missing location in a digit matrix changes, stories
are paraphrased, and answer ordering varies. Human performance is comparatively stable on
many such transformations, while GPT performance often falls sharply [Lewis and Mitchell,
2024a, 2024b]. Story judgments also show answer-order sensitivity not seen in people.

This does not imply that LLMs possess no abstract representations. It shows that successful
behavior is often entangled with surface format, pretrained familiarity, and response
conventions. For the present project, the implication is direct: an analogy selector cannot
be validated only on standard forms of the target. Source mappings must be tested under
symbol substitutions, paraphrase, and changed presentation formats.

Recent benchmarks broaden the challenge beyond compact puzzle formats. StoryAnalogy
contains 24,000 cross-domain story pairs annotated using an extension of structure-mapping
theory. Its early experiments found a large human–model gap in analogy identification,
particularly when relational alignment had to overcome surface differences [Cheng et al.,
2023]. ARN explicitly crosses near/far and analogy/disanalogy conditions in narratives;
models handle near analogies more reliably but struggle on far analogies, even when
chain-of-thought and demonstrations improve performance [Sourati et al., 2024]. AnaloBench
tests both retrieval from a large pool and reasoning over long scenarios. Model scale helps
less when contexts are long or the relevant source is a needle in a haystack [Ye et al.,
2024].

Together these studies locate the central bottleneck. Models often succeed when the source
is supplied, compact, and presented in a familiar form. They are less reliable when they
must retrieve a distant source, ignore surface distractors, preserve a mapping across
reformulations, or apply the relation to new entities. This is almost exactly the gap
between ordinary analogy prompting and directed analogical exploration.

### 2.3 Recent mechanistic behavioral analyses

_The Curious Case of Analogies_ moves from accuracy toward a decomposition of model
failure. Lee and colleagues report that correct proportional analogies are associated with
relational information in mid-to-upper layers, while incorrect cases retain attributive
information but lose or misroute relational information. Their patching experiments
implicate the token linking the first and second pair in transferring the relation to the
target. In story analogies, correct responses show stronger source–target structural
alignment; failures show weaker alignment or alignment with distractors [Lee et al., 2026].

This evidence supports a distinction that is central to the proposed paper: **representing
a relation is not the same as applying it**. A model may encode the relevant abstraction
and still fail to route it to the target entity or candidate action. An exploration system
therefore needs measurements at both points: whether a candidate activates a coherent
relation and whether that activation causally changes the proposed target transition.

## 3. Analogy as an inference-time reasoning method

Behavioral benchmarks ask whether a model can recognize or complete a supplied analogy.
A separate literature asks whether analogy can improve performance on another task.

### 3.1 Self-generated exemplars

Analogical prompting asks the model to generate several relevant solved examples or a
task-specific tutorial before solving the target. Across mathematical reasoning, code
generation, and BIG-Bench tasks, this approach outperformed zero-shot chain-of-thought and
was competitive with or better than manually supplied few-shot demonstrations [Yasunaga et
al., 2024]. The method is attractive because it requires neither fine-tuning nor an external
example library. It treats the model's pretrained knowledge as a retrievable case base.

The method's notion of analogy is broad. Generated examples are usually near-domain
problems that share an algorithm or solution pattern, rather than cross-domain mappings with
explicit roles. Its gains may come from several sources: retrieving relevant knowledge,
allocating more inference-time compute, priming a known algorithm, or creating a useful
demonstration distribution. The experiments show practical value but do not isolate which
component is analogical.

Thought Propagation uses a more explicit solve-neighbor-and-transfer pattern. The model
proposes and solves related problems, then uses their solutions to construct or revise the
target solution. Reported gains span shortest-path reasoning, planning, and creative
writing [Yu, He, and Ying, 2024]. Again, the key advance is procedural: inference is not
forced to begin from scratch. The limitation is selection. The model largely judges the
relevance and correctness of its own analogs, allowing fluent but structurally irrelevant
examples to survive.

These approaches establish a lower bound for the new proposal. Any more elaborate latent
selector should be compared against self-generated-exemplar prompting under equal total
compute. Otherwise improvements might be attributable simply to additional samples or
tokens.

### 3.2 Human-provided cross-domain bridges

Larraz and Corma provide unusually direct evidence for cross-domain guidance in a small
frozen model. They constrain Qwen-2.5-7B with retrieval from pre-1936 technical material and
ask it to reconstruct the conceptual advance behind fluid catalytic cracking. With the
retrieved context fixed, explicit guidance from grain handling and particle suspension
increases their criterion success from 10% to 100%. Content analysis suggests that the
guidance redirects use toward cross-domain principles that were present in context but
ignored by the baseline [Larraz and Corma, 2026].

The same study subtracts contrastively derived activation directions at selected layers.
Strong intervention suppresses the target synthesis despite unchanged retrieval context,
and textual guidance does not recover it. This is unusually close to the present paper's
claim that analogy works by recruiting latent representational resources rather than merely
adding an explanation after the fact.

The study is also a caution. It uses one historical engineering case, one principal model,
a highly informative human-authored analogy, and steering vectors defined partly by
positive and negative target examples. It demonstrates that an analogical bridge can unlock
available knowledge and that internal representations are necessary for this performance.
It does not yet show that a model can discover the bridge autonomously or that the same
selection rule generalizes across problems. Autonomous source discovery remains the open
problem.

### 3.3 Training-based analogical improvement

Some recent methods train models to perform explicit transfer. Self-supervised Analogical
Learning, for example, constructs cases from model successes and failures and trains
transfer from solved cases to difficult ones [Zhang et al., 2025]. Such results are relevant
as a contrast class: they ask whether analogical behavior can be learned into the weights,
whereas directed analogical exploration asks how far the same behavior can be elicited from
frozen weights and external working memory.

For small models, these approaches may be complementary. Inference-time analogy offers
reversible, auditable adaptation to a new domain; training amortizes recurring mappings.
A serious evaluation should therefore compare direct prompting, inference-time analogy,
lightweight fine-tuning, and their combination.

## 4. From text analogies to latent analogies

### 4.1 Function vectors and causal task representations

Function-vector research provides the clearest precedent for model-native analogical
structure. Todd and colleagues identify attention-head outputs that compactly represent an
in-context task. Adding the resulting vector can induce the task in different contexts,
including some zero-shot settings, and some vectors compose [Todd et al., 2024]. The result
suggests that a transformer can carry a task-level abstraction in a causally actionable
form.

Function vectors are not analogies in themselves. They encode an input–output function
inferred from demonstrations, whereas analogy requires mapping a relational system from a
source to a target. But they establish three prerequisites for latent analogical
exploration:

- a task can have a distributed but extractable internal representation;
- that representation can be compared across candidate contexts; and
- causal intervention can test whether it influences output rather than merely correlates
  with it.

This motivates scoring explicit analogy candidates through the activation changes they
induce while teacher-forcing the same proposed target action.

### 4.2 Causality is not abstraction

Recent work complicates a simple vector account. Opiełka, Rosenbusch, and Stevenson find
that function vectors for the same relation can become nearly orthogonal when the input
format changes—for example, from open-ended to multiple-choice. Using representational
similarity analysis, they identify more format-invariant “concept vectors,” but the heads
supporting invariance overlap little with the heads producing the strongest causal effects.
Their 2026 formulation makes the lesson explicit: **causality does not imply invariance**
[Opiełka, Rosenbusch, and Stevenson, 2026].

This is perhaps the most important recent result for the present project. A direction that
strongly steers a model may encode a format-conditioned policy rather than an abstract
relation. Conversely, an invariant concept detector may recognize the relation without
being able to execute it. Model-native analogy therefore requires at least two tests:

1. Does a representation remain stable across irrelevant changes in target presentation?
2. Does manipulating it causally change the mapped target action?

Neither activation distance nor patching effect is sufficient alone. The first can measure
movement without utility; the second can expose a powerful but brittle shortcut.

### 4.3 Geometric alignment and information routing

The 2026 mechanistic literature begins to characterize analogy more directly. Lee and
colleagues associate success with token-level structural alignment and show that patching
can partly restore relation transfer [Lee et al., 2026]. Work on emergent analogical
reasoning in controlled transformers decomposes the process into geometric alignment of
relations and an internal operation that transfers that structure across categories, with
qualitatively similar trends reported in pretrained LLMs [Minegishi et al., 2026]. These
papers are early and use simplified or bounded settings, but they shift the field from
asking whether relations are decodable to asking how they are moved.

The distinction between **storage**, **alignment**, and **routing** is more useful than a
search for a single “analogy vector.” A model may contain source knowledge, align source and
target roles, and still fail to route the inferred relation to the output. Directed
exploration can instrument these stages separately: embeddings for broad retrieval,
representation similarity for alignment, activation patching for routing, and a verifier
for target utility.

### 4.4 Representation engineering and its limits

Representation engineering supplies a larger family of tools for reading and controlling
population-level representations [Zou et al., 2023]. Contrastive activation vectors,
probing, patching, and steering can reveal whether a high-level feature is available and
whether changing it affects behavior. These techniques make the phrase “what would the
model reach for?” experimentally tractable: candidate sources can be ranked partly by the
internal trajectories they induce.

But a latent direction is not automatically a semantic object. Directions can be
polysemantic, prompt-dependent, sensitive to intervention strength, and entangled with
format or lexical content. Strong steering can destroy general computation rather than
selectively remove an analogy. Controls should therefore include random directions,
norm-matched unrelated directions, multiple layers, multiple prompt formats, and tests on
non-target capabilities.

## 5. Interpretability, explicit representation, and consciousness

The consciousness literature is relevant here in a limited functional sense. Mental-model
theory describes reasoning as construction and comparison of explicit possibilities;
errors often arise when a reasoner fails to represent alternatives or counterexamples
[Johnson-Laird, 2001]. Global-neuronal-workspace theories associate conscious access in
humans with information becoming widely available for report and flexible control across
otherwise specialized processes [Dehaene and Changeux, 2011; Mashour et al., 2020].

An external analogy scratchpad can be called **workspace-like** in this restricted sense.
It makes one relational interpretation persistently available to retrieval, action
generation, prediction, verification, revision, and human inspection. This is an
architectural analogy, not evidence of phenomenal consciousness. Contemporary reviews of
AI consciousness explicitly recommend evaluating theory-derived indicator properties
rather than inferring consciousness from fluent self-report [Butlin et al., 2023, 2025].
The present project neither needs nor supports a claim that an LLM experiences its
analogy.

The sharper connection is to **explicit representation**. A latent association becomes an
object of control when the system renders it as a source, role mapping, transferable
relation, predicted consequence, and limitation. This externalization resembles the
functional role of conscious access: it permits coordination and revision. Yet the text is
not guaranteed to report the internal cause faithfully.

Work on chain-of-thought faithfulness makes this caution concrete. Causal mediation studies
find that models do not always use their stated intermediate reasoning to produce their
answers [Paul et al., 2024]. Mechanistic tracing likewise shows both conceptual planning and
cases in which a model constructs a plausible argument around an answer driven by a
different influence [Lindsey et al., 2025]. Human readability is therefore necessary but
not sufficient. An interpretable analogy should be treated as a hypothesis about the
model's computation and tested by intervention, prediction, and verification.

This yields a three-way distinction:

- **latent availability:** a relation can be decoded from or induced in activations;
- **explicit accessibility:** the system can state and maintain a role mapping; and
- **faithful control:** the stated mapping causally contributes to the verified action.

The proposed system is strongest when all three align.

## 6. Synthesis: what the literature supports

The literature supports four moderate claims.

First, pretrained language models contain substantial analogical competence. They can
induce and apply many relations without task-specific fine-tuning, and self-generated
examples can improve downstream reasoning.

Second, this competence is not reliably abstract in the strong, human-like sense.
Performance remains sensitive to surface form, familiar symbols, answer order, retrieval
load, context length, and source–target distance. Scaling alone does not consistently remove
these problems.

Third, internal relational and task representations are measurable and sometimes causal.
Function vectors, concept vectors, attention knockout, activation patching, and steering
show that model behavior is not exhausted by surface text association. But invariant
representation and causal control can reside in different components.

Fourth, explicit analogy can unlock knowledge that a small frozen model already possesses
but fails to use. Existing studies mostly supply the analogy or let the model generate
near-domain examples. They do not yet solve autonomous, model-relative source selection
under external verification.

The strongest open question is therefore not “can LLMs do analogy?” It is:

> Can a frozen model search its own representational repertoire for a source structure
> that is both invariant enough to count as a relation, causal enough to change target
> inference, explicit enough to inspect, and disciplined enough to survive verification?

## 7. Research gap and contribution of directed analogical exploration

Existing approaches leave four gaps that the proposed work can address.

### 7.1 Retrieval is usually textual or human-supplied

Analogical prompting generates examples with language sampling; cross-domain studies often
provide the bridge. Directed exploration instead generates a slate and uses the target
model's embeddings and activations as additional retrieval evidence. This tests whether
model-native sources differ from canonical human metaphors.

### 7.2 Selection is rarely causal and pre-verification

Most prompting methods allow the model to choose its own analogs from their prose. A better
selector measures relevance, format invariance, activation displacement, and causal
likelihood change before seeing the verifier outcome. The verifier then supplies an honest
test rather than a selection signal that can leak the answer.

### 7.3 Interpretability and internal effect are usually studied separately

Cognitive work emphasizes explicit relational mappings; mechanistic work emphasizes latent
directions. Requiring each candidate to have both a human-readable mapping and a measured
causal signature connects the two. Disagreement is itself informative: fluent mappings with
no causal effect are likely rationalizations; causal directions with no stable mapping are
poor candidates for interpretable reasoning.

### 7.4 Few studies test iterative revision

Analogy is usually evaluated in one shot. A persistent scratchpad allows local revision
after an unhelpful action and complete source abandonment after a counterexample. This more
closely resembles a defeasible working model than a prompt ornament.

## 8. Recommended experimental claims and controls

The literature suggests that the first paper should make a constrained claim: directed
analogical exploration can improve the verified inference-time performance of a frozen
small model on sparse-rule tasks. It should not initially claim human-like analogy, general
domain learning, faithful introspection, or consciousness.

The most important controls are:

- neutral reasoning under equal action budget and equal total model compute;
- text-only self-generated analogies;
- human-authored analogies;
- shuffled or role-permuted mappings preserving prompt length and vocabulary;
- latent-only steering without an explicit mapping;
- paraphrased and symbol-substituted target states;
- held-out problem families for whitening and score calibration;
- random and norm-matched activation directions;
- independent verification of every claimed target transition; and
- preregistered abstention when no candidate has positive causal evidence.

The most diagnostic dependent measures are verified completion, verified progress per total
compute, calibration of predicted successor states, robustness across target formats,
source-domain diversity, and the association between pre-verification causal score and
later verified utility.

## 9. Evidence map

| Question | Representative evidence | What it establishes | What remains unresolved |
|---|---|---|---|
| Can LLMs solve analogy tasks? | Webb et al. (2023) | Strong zero-shot behavior across several text analogy families | Training overlap, format dependence, mechanism |
| Is performance robust? | Lewis & Mitchell (2024a,b); Sourati et al. (2024) | Counterfactual and far-transfer brittleness | Performance of newer reasoning models with matched tools |
| Can models retrieve analogies in long contexts? | Ye et al. (2024) | Retrieval and long-scenario mapping remain difficult; scale helps little in key regimes | Whether model-native retrieval signals outperform text similarity |
| Can analogy improve other tasks? | Yasunaga et al. (2024); Yu et al. (2024) | Self-generated analogs/exemplars improve reasoning and planning | Compute confounds, self-evaluation, structural faithfulness |
| Can cross-domain guidance unlock latent knowledge? | Larraz & Corma (2026) | Large gain with fixed retrieved context; internal representations are intervention-sensitive | Autonomous source discovery and broad replication |
| Are task abstractions causally encoded? | Todd et al. (2024) | Function vectors can causally induce in-context tasks | Whether they encode abstract relations rather than format-conditioned policies |
| Are causal vectors invariant? | Opiełka et al. (2026) | Causal function vectors and invariant concept vectors can be largely distinct | How to combine stable abstraction with strong control |
| Where does analogy fail internally? | Lee et al. (2026) | Failures involve relational encoding, structural alignment, and routing to the target | Generality across architectures and open-ended reasoning |
| Are verbal explanations faithful? | Paul et al. (2024); Lindsey et al. (2025) | Explicit reasoning may diverge from causal computation | Reliable faithfulness tests for open-ended analogical mappings |
| Does explicit access imply consciousness? | Butlin et al. (2023, 2025) | Consciousness claims require theory-derived indicators, not fluent report | The project should remain neutral on phenomenal consciousness |

## References

- Butlin, P., Long, R., Elmoznino, E., et al. (2023). Consciousness in
  artificial intelligence: Insights from the science of consciousness.
  https://arxiv.org/abs/2308.08708
- Butlin, P., Long, R., Bayne, T., et al. (2025). Identifying indicators of
  consciousness in AI systems. *Trends in Cognitive Sciences*.
  https://doi.org/10.1016/j.tics.2025.10.011
- Cheng, J., Qiu, L., Chan, T., et al. (2023). StoryAnalogy: Deriving story-level
  analogies from large language models to unlock analogical understanding.
  *EMNLP 2023*. https://doi.org/10.18653/v1/2023.emnlp-main.706
- Dehaene, S., and Changeux, J.-P. (2011). Experimental and theoretical
  approaches to conscious processing. *Neuron, 70*(2), 200–227.
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
- Larraz, R., and Corma, A. (2026). Human analogical guidance amplifies LLM
  performance through cross-domain knowledge activation. *Nature
  Communications, 17*, 4822. https://doi.org/10.1038/s41467-026-70873-7
- Lee, T., Song, M., Yoon, C., Park, J., and Kang, J. (2026). The curious case of
  analogies: Investigating analogical reasoning in large language models.
  *AAAI 2026*. https://arxiv.org/abs/2511.20344
- Lewis, M., and Mitchell, M. (2024a). Using counterfactual tasks to evaluate the
  generality of analogical reasoning in large language models.
  https://arxiv.org/abs/2402.08955
- Lewis, M., and Mitchell, M. (2024b). Evaluating the robustness of analogical
  reasoning in large language models. https://arxiv.org/abs/2411.14215
- Lindsey, J., Gurnee, W., Ameisen, E., et al. (2025). On the biology of a large
  language model. https://transformer-circuits.pub/2025/attribution-graphs/biology.html
- Mashour, G. A., Roelfsema, P., Changeux, J.-P., and Dehaene, S. (2020).
  Conscious processing and the global neuronal workspace hypothesis. *Neuron,
  105*(5), 776–798. https://doi.org/10.1016/j.neuron.2020.01.026
- Minegishi, G., Feng, J., Furuta, H., Kojima, T., Iwasawa, Y., and Matsuo, Y.
  (2026). Emergent analogical reasoning in transformers.
  https://arxiv.org/abs/2602.01992
- Opiełka, G., Rosenbusch, H., and Stevenson, C. E. (2026). Causality does not
  equal invariance: Function and concept vectors in LLMs. *ICLR 2026*.
  https://arxiv.org/abs/2602.22424
- Paul, D., West, R., Bosselut, A., and Faltings, B. (2024). Making reasoning
  matter: Measuring and improving faithfulness of chain-of-thought reasoning.
  https://arxiv.org/abs/2402.13950
- Sourati, Z., Ilievski, F., Sommerauer, P., and Jiang, Y. (2024). ARN:
  Analogical reasoning on narratives. *Transactions of the Association for
  Computational Linguistics, 12*, 1063–1086.
  https://doi.org/10.1162/tacl_a_00688
- Todd, E., Li, M., Sharma, A. S., Mueller, A., Wallace, B. C., and Bau, D.
  (2024). Function vectors in large language models. *ICLR 2024*.
  https://openreview.net/forum?id=AwyxtyMwaG
- Webb, T., Holyoak, K. J., and Lu, H. (2023). Emergent analogical reasoning in
  large language models. *Nature Human Behaviour, 7*, 1526–1541.
  https://doi.org/10.1038/s41562-023-01659-w
- Webb, T., Holyoak, K. J., and Lu, H. (2025). Evidence from counterfactual tasks
  supports emergent analogical reasoning in large language models. *PNAS Nexus,
  4*. https://doi.org/10.1093/pnasnexus/pgaf135
- Yasunaga, M., Chen, X., Li, Y., et al. (2024). Large language models as
  analogical reasoners. *ICLR 2024*. https://arxiv.org/abs/2310.01714
- Ye, X., Wang, A., Choi, J., et al. (2024). AnaloBench: Benchmarking the
  identification of abstract and long-context analogies. *EMNLP 2024*.
  https://doi.org/10.18653/v1/2024.emnlp-main.725
- Yu, J., He, R., and Ying, R. (2024). Thought Propagation: An analogical
  approach to complex reasoning with large language models. *ICLR 2024*.
  https://proceedings.iclr.cc/paper_files/paper/2024/hash/2dae7d1ccf1edf76f8ce7c282bdf4730-Abstract-Conference.html
- Zhou, B., Jain, S., Zhang, Y., Ning, Q., Wang, S., Benajiba, Y., and Roth, D.
  (2025). Self-supervised analogical learning using language
  models. https://arxiv.org/abs/2502.00996
- Zou, A., Phan, L., Chen, S., et al. (2023). Representation engineering: A
  top-down approach to AI transparency. https://arxiv.org/abs/2310.01405
