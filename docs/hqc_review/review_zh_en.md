# HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现

> HQC 与基于编码的后量子密码（code-based cryptography）

> 本文由 research-agent 研究链路基于现有文献文献自动生成；正文中文，摘要英文；引用编号对应文末参考文献。

---

## Abstract

- This review synthesizes evidence from the provided literature on HQC as a code-based post-quantum key encapsulation mechanism. The code-based setting is grounded in the hardness of decoding random linear codes; information set decoding (ISD) and its advanced variants are repeatedly reported as the best-known general attack family <sup>1</sup><sup>2</sup><sup>3</sup>. HQC is documented as a parameterized KEM with HQC-128, HQC-192, and HQC-256 instances, following key generation, encapsulation, and decapsulation operations <sup>4</sup><sup>5</sup>. For HQC-128, GMD/Reed-Solomon parameter optimization yields n=13829, nRS=36, a decoding failure rate below 2^-128, and a 22% key-size reduction <sup>6</sup><sup>6</sup>. Security-reduction evidence remains adjacent rather than HQC-specific: reductions such as ResSD-to-RegSD, Stern t-HVZK to syndrome decoding, and quantum stabilizer decoding to symplectic LPN appear, but a direct HQC IND-CCA reduction to a stated code problem is not available in the corpus <sup>7</sup><sup>8</sup><sup>9</sup>. Implementation evidence includes AVX2 and HVX/Hexagon optimizations that reduce runtime, as well as single-trace power side-channel attacks on HQC polynomial multiplication <sup>10</sup><sup>11</sup><sup>12</sup>. Standardization evidence shows that the NIST PQC process covers KEMs and signatures and that initial standards have been published; however, the provided corpus does not settle HQC's final version parameters <sup>13</sup><sup>5</sup>. Open problems include decoding failure attacks, HQC-specific security reduction, HQC-192/256 parameter optimization, and fair cross-platform performance benchmarking.<sup>1,2,3,4,5,6,7,8,9,10,11,12,13</sup>

## 基于编码密码学整体脉络

- 基于编码密码学的安全性依赖随机线性码解码的困难性；随机线性码解码在给定文献中被表述为 NP-hard 或 NP-complete 问题<sup>1</sup>。<sup>1</sup>

- 信息集解码（ISD）及其高级变体被多次表述为随机线性码解码的已知最优算法族<sup>2,3</sup>。<sup>2,3</sup>

- McEliece、BIKE、Classic McEliece 与 HQC 在编码方案谱系中的直接结构比较和相对位置，在给定文献库中缺乏可核验证据，暂不能作为已证实结论。（该问题在现有语料中缺少直接证据）

## HQC 构造与参数集

- HQC 被记录为一种基于编码的 KEM，具有 HQC-128、HQC-192、HQC-256 等参数化安全级别或实例<sup>4,5,14</sup>。<sup>4,5,14</sup>

- HQC 的 KEM 抽象流程包括密钥生成、封装与解封装，形成公钥到密文/共享秘密、私钥加密文到共享秘密的转换<sup>5,4,14</sup>。<sup>4,5,14</sup>

- HQC-128 的 GMD/RS 参数优化已给出 nRS=36、n=13829，并控制 DFR 低于 2^-128，同时报告密钥长度减少 22%<sup>6</sup>。<sup>6</sup>

- HQC-192 与 HQC-256 的同类 GMD/RS 参数优化证据不足，不能将 HQC-128 的优化结论直接推广到更高安全级别。（该问题在现有语料中缺少直接证据）

## 安全归约与困难假设

- 现有文献邻近编码场景给出若干安全归约：ResSD 到 RegSD、t-HVZK 到 syndrome decoding、量子稳定子解码到 symplectic LPN 等<sup>7,8,9</sup>。<sup>7,8,9</sup>

- 在结构化或量子稳定子解码等邻近场景中，结构假设被报告为归约障碍或未给出归约<sup>9,15</sup>，但这不等同于 HQC 的归约状态。<sup>9,15</sup>

- HQC 特定 IND-CCA 安全归约至 Hamming quasi-cyclic syndrome decoding 的直接证据缺失；现有 ResSD、Stern、量子稳定子归约不能被替代为 HQC 特定证明。（该问题在现有语料中缺少直接证据）

## ISD 族攻击

- ISD 是随机线性码解码的已知最优攻击范式，其复杂度可用于估计基于编码方案的安全级别<sup>2,3,16</sup>。<sup>2,3,16</sup>

- ISD 变体在单篇证据中被描述为使用已知零位置、错误矩阵或伴随式等参数；这些机制细节属于低覆盖度证据，需要进一步验证<sup>17,18,19</sup>。<sup>17,18,19</sup>（该机制目前为推断，尚待实验验证）

- ISD 族攻击在 HQC 具体参数集上的复杂度评估在当前证据中缺失。（该问题在现有语料中缺少直接证据）

## 解码失败攻击与 DFR

- HQC-128 参数优化把 DFR 作为约束之一，目标低于 2^-128<sup>6</sup>。<sup>6</sup>

- 解码失败攻击的威胁模型、复杂度及其对 HQC 参数集的具体影响，在给定文献库中没有直接 HQC 攻击证据；不能由通用 RSR/MBBP-LD 邻近证据推导。（该问题在现有语料中缺少直接证据）

## 实现与侧信道

- HQC 的优化实现证据包括 AVX2 上的稀疏×稠密乘法、表驱动 Reed-Solomon 编解码等，并报告总运行时间降低<sup>10</sup>。<sup>10</sup>

- HVX/Hexagon 平台上的优化报告解码加速；能耗与 CPU 占用降低的表述主要来自 相关文献，不应扩展为所有平台结论<sup>11</sup>。<sup>11</sup>

- 独立侧信道证据显示单迹 SPA 可针对 HQC 多项式乘法并恢复秘密稀疏向量比特<sup>12,17</sup>。<sup>12,13,17,20,21</sup>

- 优化实现声称增强侧信道抗性，但同一现有文献中存在单迹 SPA 攻击证据，二者形成条件冲突，说明防护有效性不能作为普遍结论<sup>10,12,17</sup>。<sup>10,12,17</sup>（该机制目前为推断，尚待实验验证）

- 系统化的常时实现、掩码防护有效性及性能代价评估仍缺乏 HQC 特定证据。（该问题在现有语料中缺少直接证据）

## 标准化与迁移路径

- NIST PQC 标准化项目覆盖 KEM 与数字签名方案<sup>13,5</sup>。<sup>5,13</sup>

- 现有文献证据显示 NIST 已发布首批后量子密码标准<sup>5</sup>。<sup>5</sup>

- HQC 在现有文献同时表现为被优化对象和 KEM 安全级别讨论对象<sup>10,4</sup>；IES/KEM 效率比较也构成标准化语境下的性能参考<sup>14</sup>。<sup>4,10,14</sup>

- 给定证据未包含 HQC 最终轮次或标准化版本参数的裁决文本；因此正文不将 HQC 的最终地位作为已证实结论。（该问题在现有语料中缺少直接证据）

## 性能对比

- OptHQC 与 HQC 参考实现的基准比较显示优化可降低运行时间<sup>10</sup>。<sup>10</sup>

- HVX/Hexagon 解码加速和能耗/CPU 下降证据存在，但平台条件不同，不能直接形成统一性能排序<sup>11</sup>。<sup>11</sup>（该机制目前为推断，尚待实验验证）

- IES 效率比较与 HQC 平台优化证据之间存在条件冲突：不同实现优化程度和平台会改变 HQC 的性能位置<sup>14,10,11</sup>。<sup>10,11,14</sup>（该机制目前为推断，尚待实验验证）

- 缺少同一平台、统一安全等级下 HQC 与 BIKE、Classic McEliece 等基于编码 KEM 的公平基准比较。（该问题在现有语料中缺少直接证据）

## 开放问题与证据缺口

- HQC 特定安全归约与归约紧致性：需要 HQC 到明确编码困难问题的直接归约证据。（该问题在现有语料中缺少直接证据）

- ISD 族攻击在 HQC 参数集上的具体复杂度：现有 ISD 证据多为通用或邻近方案。（该问题在现有语料中缺少直接证据）

- 解码失败攻击对 HQC 的威胁模型与参数影响：缺少 HQC 特定攻击与 DFR 桥接证据。（该问题在现有语料中缺少直接证据）

- HQC-192/256 参数优化与标准化版本迁移路径：现有参数优化集中于 HQC-128，标准化最终版本证据不足。（该问题在现有语料中缺少直接证据）

- 实现防护有效性与性能代价：需在统一威胁模型和基准下评估常时或掩码实现。（该问题在现有语料中缺少直接证据）

## 参考文献

(1) Bolkema, J.; Gluesing-Luerssen, H.; Kelley, C. A.; Lauter, K.; Malmskog, B.; Rosenthal, J. Variations of the McEliece Cryptosystem. *arXiv* 2016. https://doi.org/10.1007/978-3-319-63931-4_5

(2) Döttling, N.; Dowsley, R.; Müller-Quade, J.; Nascimento, A. C. A. A CCA2 Secure Variant of the McEliece Cryptosystem. *arXiv* 2012. https://doi.org/10.1109/tit.2012.2203582

(3) Santini, P.; Baldi, M.; Chiaraluce, F. Assessing and countering reaction attacks against post-quantum public-key cryptosystems based on QC-LDPC codes. *arXiv* 2018. https://doi.org/10.1007/978-3-030-00434-7_16

(4) Turino, C.; Buchanan, W. J.; Lo, O.; Thuummler, C. PQC-LEO: An Evaluation Framework for Post-Quantum Cryptographic Algorithms. *arXiv* 2026. https://doi.org/10.1109/TPS-ISA67132.2025.00033

(5) Battarbee, C.; Striecks, C.; Perret, L.; Ramacher, S.; Verhaeghe, K. Quantum-Safe Hybrid Key Exchanges with KEM-Based Authentication. *arXiv* 2024. https://doi.org/10.1140/epjqt/s40507-025-00425-3

(6) Cai, J.; Zhang, X. HQC Post-Quantum Cryptography Decryption with Generalized Minimum-Distance Reed-Solomon Decoder. *arXiv* 2026.

(7) Burle, É.; Udovenko, A. Cross-Paradigm Models of Restricted Syndrome Decoding with Application to CROSS. *arXiv* 2026. https://doi.org/10.1007/978-3-032-22695-2_7

(8) Chailloux, A.; Etinski, S. On the (In)security of optimized Stern-like signature schemes. *arXiv* 2024. https://doi.org/10.1007/S10623-023-01329-Y

(9) Lu, J. Z.; Poremba, A.; Quek, Y.; Ramkumar, A. Post-Quantum Cryptography from Quantum Stabilizer Decoding. *arXiv* 2026.

(10) Dong, B.; Feng, H.; Wang, Q. OptHQC: Optimize HQC for High-Performance Post-Quantum Cryptography. *arXiv* 2025.

(11) Chau, V. M.; Kiet, N. N.; Minh, P. Q.; Ngoc, M. X.; Anh, N. D.; Ta, H. Implementation and Optimization of HQC Decoding on NPU-Integrated Devices. *arXiv* 2026. https://doi.org/10.7467/ksae.2026.34.8.865

(12) Velek, P.; Rabas, T.; Buček, J. Simple Power Analysis of Polynomial Multiplication in HQC. *arXiv* 2026.

(13) Park, J.; Ju, J.; Lee, W.; Kang, B.; Kachi, Y.; Sakurai, K. A Statistical Verification Method of Random Permutations for Hiding Countermeasure Against Side-Channel Attacks. *arXiv* 2023. https://doi.org/10.1016/j.jisa.2024.103797

(14) Chen, A. C. H. Key Encapsulation Mechanism-Based Integrated Encryption Scheme (KEM-IES). *arXiv* 2026.

(15) Melo, V. D. The HyperFrog Cryptosystem: High-Genus Voxel Topology as a Trapdoor for Post-Quantum KEMs. *arXiv* 2026. https://doi.org/10.5281/zenodo.18502099

(16) Gassner, N.; Lieb, J.; Mazumder, A.; Schaller, M. Information-Set Decoding for Convolutional Codes. *arXiv* 2024. https://doi.org/10.1007/s10623-025-01649-1

(17) Banegas, G.; Smith, B.; Zahreddine, J. Exploiting Load/Store Leakage of Sparse Vectors for Key Recovery in HQC. *arXiv* 2026.

(18) Elleuch, M.; Wachter-Zeh, A.; Zeh, A. A Public-Key Cryptosystem from Interleaved Goppa Codes. *arXiv* 2018.

(19) Yackushenoks, K.; Ivanov, F. Cryptoanalysis McEliece-type cryptosystem based on correction of errors and erasures. *arXiv* 2023. https://doi.org/10.1109/redundancy59964.2023.10330197

(20) Adjonyo, O.; Bardin, S.; Bellini, E.; Dione, G. N.; Ameen, M. F. A.; Merget, R.; Recoules, F.; Sellami, Y. Systematic Timing Leakage Analysis of NIST PQDSS Candidates: Tooling and Lessons Learned. *arXiv* 2025.

(21) Weger, V.; Gassner, N.; Rosenthal, J. A Survey on Code-Based Cryptography. *arXiv* 2022. https://doi.org/10.1007/978-3-030-98365-9
