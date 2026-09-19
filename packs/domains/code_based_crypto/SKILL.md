# domain skill：code_based_crypto（基于编码的密码学）

> 本文件是**领域知识与设计说明**（给人/Agent 读）；机器读取的是同目录的
> `domain.json` 与 `vocab/*.jsonl`。新增学科照此结构建目录即可，**无需改 Python**。

## 一、领域界定

以 **HQC（Hamming Quasi-Cyclic）** 为核心，覆盖基于编码的密码学整体：

- 方案族：HQC、BIKE（QC-MDPC）、Classic McEliece（二进制 Goppa）、Niederreiter、
  LEDAcrypt 等；
- 底层结构：quasi-cyclic / QC-MDPC / QC-LDPC / Goppa / Reed–Muller /
  Reed–Solomon / 级联与张量积码；
- 困难性：syndrome decoding（SDP）、quasi-cyclic SDP、LPN；
- 攻击：ISD 族（Prange/Stern/Dumer/BJMM/May–Ozerov）、反应攻击（GJS）、
  密钥恢复、代数攻击、侧信道（计时/功耗/缓存/故障注入）；
- 工程与标准化：FO 变换、IND-CCA2、DFR、常数时间实现、NIST PQC 与 ISO/ETSI。

## 二、节点分布（实体类型 → 角色）

| 分组 | 实体类型 | 说明 |
|---|---|---|
| 方案层 | `Scheme`、`KEM`、`PKE`、`Protocol` | HQC、BIKE、Classic McEliece… |
| 结构层 | `CodeFamily`、`ParameterSet`、`HardProblem`、`Assumption` | QC-MDPC、Goppa、SDP、QC-SDP |
| 算法层 | `Decoder`、`Attack`、`AttackAlgorithm`、`Algorithm`、`Transform` | BF/BP+OSD、ISD 变体、FO |
| 安全层 | `SecurityModel`、`SecurityLevel`、`ProofTechnique` | ROM/QROM、NIST 1/3/5、归约 |
| 度量层 | `Metric`、`Benchmark`、`Dataset` | work factor、DFR、cycles |
| 工程层 | `Implementation`、`Platform`、`Standard`、`StandardizationBody`、`SideChannel`、`Countermeasure` | AVX2 实现、NIST、计时攻击 |

**分布原则**：方案与码族是枢纽节点（多数关系经过它们）；攻击与解码器是"动词侧"，
必须与具体方案/码族相连；度量节点只挂在超边的 measurements 上，**不单独建孤立度量节点**。

## 三、超边形式（本领域的"机制"= 归约与攻击/解码路径）

| 超边类型 | 成员角色 | conditions | measurements |
|---|---|---|---|
| `security_reduction` | scheme / assumption / hard_problem / security_model / proof_technique | security_model, security_level, quantum_model, reduction_type | reduction_loss, tightness, security_margin |
| `attack` | attack / attack_algorithm / target_scheme / decoder / side_channel | attack_model, quantum_model, n, k, w, t, data_complexity, memory, iterations | work_factor(log2), time_complexity(log2), memory, success_probability, query_count, speedup |
| `construction` | scheme / code_family / decoder / transform / hard_problem | security_level, n, k, w, t, d, field, parameter_set | public_key_size, secret_key_size, ciphertext_size, bandwidth, dfr |
| `parameterization` | scheme / parameter_set / security_level | security_level, parameter_set, n, k, w, t | public_key_size, ciphertext_size, secret_key_size, dfr, security_margin |
| `failure_analysis` | decoder / code_family / scheme / error_pattern | n, k, w, t, d, iterations, parameter_set | dfr(log2), failure_rate, decoding_iterations, success_probability |
| `benchmark` | implementation / scheme / platform / parameter_set | cpu, compiler, implementation, platform, security_level | keygen_cycles, encaps_cycles, decaps_cycles, throughput, latency, memory, speedup |

**侧信道**不单独建类型：以 `attack` + `attack_model = timing/power/cache/fault` 表达，
防护措施作为 `Countermeasure` 实体参与 `mitigates` 关系。

**语义约定（写进提示词）**：
- 复杂度一律写 `2^x`，指数进 measurements 数值，`unit = log2`；
- `dfr` 用 `log2`（如 `2^-128` → 值 `-128`，unit `log2`）；
- 尺寸统一 `bytes`，周期数统一 `cycles`，禁止把数字塞进 `attributes`。

## 四、词表

- `vocab/terms.jsonl`：40 条领域术语身份（方案/码族/攻击/解码器/变换/度量/模型），
  `source = local:code-based-crypto`，用于别名归一与复用规范名。
- `vocab/mechanism.jsonl`：机制层增补。本领域对化学口径的词表用
  `"mode": "replace"` **整体替换**（继承化学的"温度/溶剂/umpolung"会污染抽取），
  再把 `mechanism_keywords` 重定义为归约/解码/攻击路径短语，`operator_keywords`
  重定义为密码学算子（keygen/encaps/decaps/syndrome/ISD/FO/级联/常数时间…）。

## 五、数据集与检索

- 主要来源：arXiv（cs.CR、math.IT、cs.IT）、OpenAlex / Semantic Scholar（期刊与会议：
  PQCrypto、IEEE Trans. Inf. Theory、Designs Codes and Cryptography、ASIACRYPT 等）；
- PubMed / Europe PMC 与本领域无关，不启用；
- 检索词示例见 `docs/HQC_CORPUS_QUERIES.md`。
