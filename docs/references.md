# References

## The Paper

- **Odrzywołek 2026.** "All elementary functions from a single operator."
  arXiv:2603.21852v2. April 2026.
  https://arxiv.org/abs/2603.21852
  Code: https://github.com/VA00/SymbolicRegressionPackage

## Geometric / Bayesian Inference

- **Betancourt 2017.** "A Conceptual Introduction to Hamiltonian Monte Carlo."
  arXiv:1701.02434.
  Key: pathological geometry (not dimensionality) kills inference. Funnel
  degeneracies, reparameterization.

- **Betancourt & Girolami 2013.** "HMC for Hierarchical Models."
  arXiv:1312.0906.
  Key: Riemannian manifold HMC, geometry-aware sampling.

- **Betancourt case studies.** Identifying mixture models, identifiability.
  https://betanalpha.github.io/writing/
  Key: symmetry-breaking constraints, label-switching, soft non-identifiability.

- **Khan & Lin 2019.** Natural gradient VI with mixture of exponential families.
  ICML 2019.
  Key: natural gradient convergence independent of Fisher conditioning.

- **"Beyond Softmax" 2025.** arXiv:2509.24728.
  Key: hierarchical binary-split parameterization makes Fisher diagonal for
  categorical distributions.

- **Liu & Wang 2016.** "Stein Variational Gradient Descent." arXiv:1608.04471.

- **Branching SVGD 2025.** arXiv:2506.13916.
  Key: particle ensemble with branching for multimodal landscapes.

## Architecture Search (DARTS Parallel)

- **Liu et al. 2019.** "DARTS: Differentiable Architecture Search."
  arXiv:1806.09055.
  Key: softmax over discrete choices at each node -- structurally identical to
  EML master formula. Known failure modes.

- **Jang et al. 2017.** "Categorical Reparameterization with Gumbel-Softmax."
  arXiv:1611.01144.
  Cited in EML paper. Annealing from continuous to discrete.

- **GAEA.** Geometry-aware exponentiated gradient for NAS.
  https://github.com/liamcli/gaea_release

## Hardware

- **Arnold & Collange 2009.** "Dual-Purpose Real/Complex LNS ALU."
  Arith19. Key: complex log-polar arithmetic from reused real LNS ALU.
  https://www.irisa.fr/alf/downloads/collange/papers/ArCo_Arith19.pdf

- **GreenArrays GA144.** 144-core async FORTH processor. ~7 pJ/instruction.
  https://www.greenarraychips.com/home/documents/greg/GA144.htm

- **J1 Forth CPU.** ~200 LUTs on FPGA. 80MHz Spartan-3E.
  https://excamera.com/files/j1.pdf

- **LNS-Madam (NVIDIA 2022).** 5-bit LNS gradients matching full-precision
  accuracy on ResNet-50/BERT.

- **GSGP-Hardware 2024.** FPGA genetic programming accelerator, 4,902x speedup.
  Springer. Key precedent for symbolic regression hardware.

## Approximation Theory

- **Poggio et al. 2017.** "Why and when can deep networks avoid the curse of
  dimensionality." PNAS. Key: compositional sparsity -> poly(d) rates.

- **Telgarsky 2016.** "Benefits of depth in neural networks." arXiv:1602.04485.
  Key: depth-k network represents functions needing exp(k) nodes in shallow nets.

- **Kong & Chaudhuri 2020.** "Expressive Power of Normalizing Flow Models."
  AISTATS. Key: affine coupling flows are universal but depth grows as O(d^kappa);
  4 layers can't represent all linear maps for d >= 4.

- **Papamakarios et al. 2021.** "Normalizing Flows for Probabilistic Modeling
  and Inference." JMLR survey.

- **Liu et al. 2024.** "KAN: Kolmogorov-Arnold Networks." ICLR 2025.
  arXiv:2404.19756.

## Symbolic Regression

- **Cranmer 2023.** PySR / SymbolicRegression.jl. arXiv:2305.01582.

- **Udrescu & Tegmark 2020.** "AI Feynman." Science Advances.
