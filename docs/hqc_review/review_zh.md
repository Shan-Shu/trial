# HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现

> HQC 与基于编码的后量子密码（code-based cryptography）

> 本文由 research-agent 研究链路基于现有文献文献自动生成；引用编号对应文末参考文献。

---

## 摘要

本综述在给定现有文献证据上归纳基于编码后量子密码的整体脉络，并以 HQC 为焦点。已有证据表明 HQC 是基于 Hamming Quasi-Cyclic 的 KEM，支持多档安全级别；实现优化可显著降低运行时间；单迹 SPA 已对 HQC 实现形成攻击面；ISD 族攻击是随机线性码解码的主流方法，并被记录为 McEliece、Niederreiter 等方案的攻击手段。直接以 HQC 为目标的安全归约、ISD 复杂度估计与解码失败攻击实验证据仍不足，相关判断应标为开放问题。

## 基于编码密码学的历史脉络与困难问题

解码随机线性码是编码密码学的基础困难问题之一；现有文献多处证据显示，最优已知算法属于信息集解码（ISD）族。<sup>1,2</sup>
在汉明度量下，最优 SDP 解算器被记录为 ISD 算法，并包括 Prange、Stern、BJMM 等变体。<sup>2</sup>
McEliece 与 Niederreiter 等经典编码密码方案均被 ISD 攻击直接作为目标。<sup>3</sup>
编码密码学安全性与解码问题的 NP 困难性相关，但现有文献证据仅支持这一依赖关系，未给出具体参数安全等价。<sup>4</sup>
受限综合征解码问题（RSDP）同样被记录为受 ISD 风格算法及改进攻击。<sup>1,5</sup>
ISD 攻击不仅作为攻击算法出现，也被用于评估安全级别和具体安全性。<sup>6,7</sup>

## HQC 具体构造、码族与参数集

HQC 被描述为基于 Hamming Quasi-Cyclic 的密钥封装机制，并支持 HQC-128/HQC-192/HQC-256 三档安全级别。<sup>8</sup>
现有文献参数命名不一致：优化基准超边中同时出现 HQC-128 与 HQC-3/HQC-5，说明版本或命名体系存在差异。<sup>9</sup>
Reed-Solomon 编码/解码组件被列为 HQC 优化过程的一部分，其中表驱动 Reed-Solomon 编码/解码器是已记录的优化对象。<sup>9</sup>
HQC-128 被用于 KEM-IES 混合构造，与 ML-KEM-512 共同组成混合加密方案。<sup>10</sup>
HQC-128 参数/DFR 优化将 RS 码字长度从 46 降至 36，将 n 从 17669 降至 13829，并报告密钥长度减少约 22% 且 DFR 低于 2^-128。<sup>11</sup>
具体的 concatenated Reed-Muller/Reed-Solomon 组合、HHK transform 和 implicit rejection 细节未在当前现有文献给出直接证据，因此暂标为开放问题。<sup>9</sup>（该问题在现有语料中缺少直接证据）

## 安全归约与困难性假设

已有安全归约证据集中在 Stern 签名方案的 t-HVZK 到随机线性码解码，以及量子 stabilizer decoding 到 symplectic LPN。<sup>12,13</sup>
通用 KEM 构造依赖计算假设这一关系已在现有文献被记录，但这不构成 HQC 的具体归约证明。<sup>14</sup>
HQC 到 syndrome decoding/QCSD 或 IND-CCA/IND-CCA2 的完整归约、损失与紧致性在当前现有文献缺少机制级证据，应视为开放问题。<sup>15</sup>（该问题在现有语料中缺少直接证据）
相关文献 仅记录 HQC 优化流程与组件替换，不包含完整安全归约证明，不能作为归约紧致性证据使用。<sup>9</sup>

## ISD 族攻击

ISD 是随机线性码解码的主流攻击算法族，现有文献多处证据支持这一判断。<sup>1,2</sup>
McEliece 与 Niederreiter 密码系统被记录为 ISD 攻击目标。<sup>3</sup>
结构化码并非天然对 ISD 有效；McNie 被记录具有抵抗结构化和信息集解码攻击的属性。<sup>16</sup>
当前现有文献未出现直接以 HQC 为目标的 ISD 复杂度估计或攻击实验；将通用 SDP/McEliece 的 ISD 结果外推至 HQC 存在目标对象不匹配风险，标为开放问题。<sup>1,8</sup>（该问题在现有语料中缺少直接证据）

## 解码失败攻击

HQC-128 参数/DFR 优化中，通过 GMD 解码与 RS 码字长度调整，使 DFR 低于 2^-128，并同步缩小密钥长度。<sup>11</sup>
现有文献没有针对 HQC 的解码失败攻击模型、可恢复信息或攻击复杂度证据；因此解码失败攻击对 HQC 的具体影响为开放问题。<sup>11</sup>（该问题在现有语料中缺少直接证据）

## 实现与侧信道

OptHQC 在 x86 上使用 AVX2、SHAKE 与查表 Reed-Solomon 编解码等优化，总运行时间降低 50-60%；相关优化被记录为总运行时间降低的原因。<sup>9</sup>
HVX 在 Snapdragon 平台上优化 HQC 解码，给出约 2 倍左右加速，但测试平台与 x86 优化条件不同。<sup>17</sup>
HQC 参考实现存在单迹 SPA 攻击面；现有文献证据显示 10000 次攻击尝试成功率为 99.69%，31 次失败。<sup>18,19</sup>
侧信道攻击更广泛地以后量子密码与基于编码密码为对象。<sup>20,21,22</sup>
由于 OptHQC 和 HVX 的测试平台、参数命名和条件不一致，跨平台性能结论不能直接合并。<sup>9,17</sup>

## 标准化与迁移

现有文献超边显示 KEM 被纳入 NIST PQC 标准化进程，NIST PQC 标准化事件覆盖密钥封装机制和数字签名方案。<sup>14,21</sup>
HQC-128 在混合加密集成中被用作 KEM-IES 的组成 KEM。<sup>10</sup>
现有文献对 HQC 独立标准化状态、第四轮选择以及 2025-03-11 选择信息的直接证据不足；应把具体版本状态标为开放问题。<sup>14,21</sup>（该问题在现有语料中缺少直接证据）

## 性能代价对比

现有文献比较超边记录了 ML-KEM、HQC、ECIES 与 Hybrid IES 的效率比较，HQC 在该比较中效率最低；但测试平台和比较对象存在差异。<sup>10</sup>
HQC-128 与 ML-KEM-512 组成 KEM-IES 的证据表明混合加密性能比较涉及不同密码学原语的组合。<sup>10</sup>
当前性能数据以特定平台和实现版本为基准，不能直接用于跨方案因果排序；性能排序结论应保持为受条件约束的假设。<sup>9,10,17</sup>（该机制目前为推断，尚待实验验证）

## 挑战与开放问题

HQC 的紧致安全归约与具体参数安全边界是否充分仍为开放问题。<sup>14,15</sup>（该问题在现有语料中缺少直接证据）
解码失败率在有限长度和具体参数下的界是否紧，现有文献只有参数优化证据，无紧性证明。<sup>11</sup>（该问题在现有语料中缺少直接证据）
侧信道防护与高性能实现之间的标准化折中缺少现有文献直接证据。<sup>18,20</sup>（该问题在现有语料中缺少直接证据）
从经典编码密码到 HQC 的迁移成本与互操作性缺现有文献直接证据。<sup>14,21</sup>（该问题在现有语料中缺少直接证据）
长期量子攻击模型下的参数更新策略缺现有文献直接证据。<sup>14</sup>（该问题在现有语料中缺少直接证据）

## 参考文献

(1) Döttling, N.; Dowsley, R.; Müller-Quade, J.; Nascimento, A. C. A. A CCA2 Secure Variant of the McEliece Cryptosystem. *arXiv* 2012. https://doi.org/10.1109/tit.2012.2203582
(2) Santini, P.; Baldi, M.; Chiaraluce, F. Assessing and countering reaction attacks against post-quantum public-key cryptosystems based on QC-LDPC codes. *arXiv* 2018. https://doi.org/10.1007/978-3-030-00434-7_16
(3) Kapshikar, U. McEliece-type Cryptosystems over Quasi-cyclic Codes. *arXiv* 2018.
(4) Bolkema, J.; Gluesing-Luerssen, H.; Kelley, C. A.; Lauter, K.; Malmskog, B.; Rosenthal, J. Variations of the McEliece Cryptosystem. *arXiv* 2016. https://doi.org/10.1007/978-3-319-63931-4_5
(5) Arpin, S.; LeGrow, J. T.; López, H. H.; Matthews, G. L. Digital signature schemes based on code equivalence and syndrome decoding from restricted errors. *arXiv* 2026. https://doi.org/10.1109/MBITS.2026.3706068
(6) Jerkovits, T.; Hörmann, F.; Bartz, H. On Decoding High-Order Interleaved Sum-Rank-Metric Codes. *arXiv* 2023. https://doi.org/10.1007/978-3-031-29689-5
(7) Wu, H.; Zhuang, J. Syndrome decoding meets multiple instances. *arXiv* 2022.
(8) Turino, C.; Buchanan, W. J.; Lo, O.; Thuummler, C. PQC-LEO: An Evaluation Framework for Post-Quantum Cryptographic Algorithms. *arXiv* 2026. https://doi.org/10.1109/TPS-ISA67132.2025.00033
(9) Dong, B.; Feng, H.; Wang, Q. OptHQC: Optimize HQC for High-Performance Post-Quantum Cryptography. *arXiv* 2025.
(10) Chen, A. C. H. Key Encapsulation Mechanism-Based Integrated Encryption Scheme (KEM-IES). *arXiv* 2026.
(11) Cai, J.; Zhang, X. HQC Post-Quantum Cryptography Decryption with Generalized Minimum-Distance Reed-Solomon Decoder. *arXiv* 2026.
(12) Chailloux, A.; Etinski, S. On the (In)security of optimized Stern-like signature schemes. *arXiv* 2024. https://doi.org/10.1007/S10623-023-01329-Y
(13) Lu, J. Z.; Poremba, A.; Quek, Y.; Ramkumar, A. Post-Quantum Cryptography from Quantum Stabilizer Decoding. *arXiv* 2026.
(14) Panja, S.; Sharifian, S.; Jiang, S.; Safavi-Naini, R. CCA-Secure Hybrid Encryption in Correlated Randomness Model and KEM Combiners. *arXiv* 2024. https://doi.org/10.1016/j.tcs.2025.115518
(15) Battarbee, C.; Striecks, C.; Perret, L.; Ramacher, S.; Verhaeghe, K. Quantum-Safe Hybrid Key Exchanges with KEM-Based Authentication. *arXiv* 2024. https://doi.org/10.1140/epjqt/s40507-025-00425-3
(16) Kim, J.; Kim, Y.; Galvez, L.; Kim, M. J.; Lee, N. McNie: A code-based public-key cryptosystem. *arXiv* 2018. https://doi.org/10.1109/ccst.1991.202206
(17) Chau, V. M.; Kiet, N. N.; Minh, P. Q.; Ngoc, M. X.; Anh, N. D.; Ta, H. Implementation and Optimization of HQC Decoding on NPU-Integrated Devices. *arXiv* 2026. https://doi.org/10.7467/ksae.2026.34.8.865
(18) Velek, P.; Rabas, T.; Buček, J. Simple Power Analysis of Polynomial Multiplication in HQC. *arXiv* 2026.
(19) Banegas, G.; Smith, B.; Zahreddine, J. Exploiting Load/Store Leakage of Sparse Vectors for Key Recovery in HQC. *arXiv* 2026.
(20) Adjonyo, O.; Bardin, S.; Bellini, E.; Dione, G. N.; Ameen, M. F. A.; Merget, R.; Recoules, F.; Sellami, Y. Systematic Timing Leakage Analysis of NIST PQDSS Candidates: Tooling and Lessons Learned. *arXiv* 2025.
(21) Park, J.; Ju, J.; Lee, W.; Kang, B.; Kachi, Y.; Sakurai, K. A Statistical Verification Method of Random Permutations for Hiding Countermeasure Against Side-Channel Attacks. *arXiv* 2023. https://doi.org/10.1016/j.jisa.2024.103797
(22) Weger, V.; Gassner, N.; Rosenthal, J. A Survey on Code-Based Cryptography. *arXiv* 2022. https://doi.org/10.1007/978-3-030-98365-9
