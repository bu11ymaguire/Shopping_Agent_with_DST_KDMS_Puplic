# Post-hoc Gold-State Oracle recommendation analysis

상태: **사후 보조 진단 완료**

에피소드 종료 Gold-State를 동결된 Query→Browse→semantic review retrieval→Rank 경로에 주입했다. Understanding과 Response Composer를 호출하지 않았으므로 추가 LLM 호출은 0회다.

> 이 결과는 Gold 추천 상품이나 relevance 정답이 아니다. 현재 결정론적 recommender가 annotated state를 받았을 때의 oracle-conditioned 출력이다.

## Summary

| Comparison | Comparable episodes | Top-1 agreement | Exact Top-3 order | Exact Top-3 set | Mean overlap | Mean Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Oracle vs Full | 15 | 0.333 | 0.333 | 0.400 | 1.600 | 0.540 |
| Oracle vs No-memory | 13 | 0.077 | 0.000 | 0.000 | 0.538 | 0.133 |

- Oracle recommendation episodes: 19/20
- Oracle Top-3 Gold hard-filter violations: 0/54
- Semantic retrieval without fallback: 20/20

## Episode-level final lists

| Episode | Gold-State Oracle Top-3 | Full final Top-3 | Jaccard | No-memory final Top-3 | Jaccard |
| --- | --- | --- | ---: | --- | ---: |
| th01 | `B0B7B53HM4` → `B08BH87FPJ` → `B07HRYDNLZ` | `B08BH87FPJ` → `B0B7B53HM4` → `B07CFWLW3G` | 0.500 | `B008SYWFNA` → `B07G9QNT6P` → `B00P2N01CW` | 0.000 |
| th02 | `B0134RE54W` → `B07G9QNT6P` → `B07HRYDNLZ` | `B00G6D6HBG` → `B0134RE54W` → `B01N13866H` | 0.200 | `B08BH87FPJ` → `B08N6T81FB` → `B08KFHS3QQ` | 0.000 |
| th03 | `B086QQNRT4` | `B086QQNRT4` | 1.000 | `B09HTM917T` → `B09Q4RTSLX` → `B086QQNRT4` | 0.333 |
| th04 | `B0B7B53HM4` → `B09XM8QVKC` → `B09TVR1YBQ` | `B0B7B53HM4` → `B09XM8QVKC` → `B09TVR1YBQ` | 1.000 | `B00CPJHGTM` → `B0117U82EM` → `B07SPHMZF5` | 0.000 |
| th05 | `B0B7B53HM4` → `B0BQDTZDP6` → `B08BH87FPJ` | `B0B7B53HM4` → `B0BQDTZDP6` → `B08BH87FPJ` | 1.000 | missing_final_turn | N/A |
| th06 | `B00G6D6HBG` → `B01N13866H` → `B017TK4H6G` | `B09TVR1YBQ` → `B0C62BKQL3` → `B08F69779X` | 0.000 | `B00G6D6HBG` → `B09832Q4VK` → `B01N13866H` | 0.500 |
| th07 | `B00G6D6HBG` → `B017TK4H6G` → `B01N13866H` | `B017TK4H6G` → `B01N13866H` → `B01N24N9TH` | 0.500 | `B017TK4H6G` → `B01N13866H` → `B07CFWLW3G` | 0.500 |
| th08 | `B0B7B53HM4` → `B0BQDTZDP6` → `B0BRJ8M7SK` | missing_final_turn | N/A | missing_final_turn | N/A |
| th09 | `B08NWY6LQ4` → `B09XM8QVKC` → `B01N24N9TH` | `B017TK4H6G` → `B00G6D6HBG` → `B01N24N9TH` | 0.200 | `B00G6D6HBG` → `B017TK4H6G` → `B01N24N9TH` | 0.200 |
| th10 | `B087BC4DJH` → `B09832Q4VK` → `B0BLTXYYF1` | `B00G6D6HBG` → `B087BC4DJH` → `B09832Q4VK` | 0.500 | `B0045FM6SU` → `B01CPZB0ZG` → `B087BC4DJH` | 0.200 |
| th11 | `B07HRYDNLZ` → `B07SPHMZF5` → `B08NWY6LQ4` | missing_final_turn | N/A | missing_final_turn | N/A |
| th12 | `B0B7B53HM4` → `B09XM8QVKC` → `B08BH87FPJ` | `B0B7B53HM4` → `B09XM8QVKC` → `B08BH87FPJ` | 1.000 | clarify-lane | N/A |
| th13 | `B0B7B53HM4` → `B0BQDTZDP6` → `B0BRJ8M7SK` | `B086QQNRT4` → `B09Q4RTSLX` → `B09HTM917T` | 0.000 | `B09HTM917T` → `B09Q4RTSLX` → `B086QQNRT4` | 0.000 |
| th14 | `B00G6D6HBG` → `B09832Q4VK` → `B0B8VH98G3` | `B07H8ZCSL9` → `B08YXW4CNS` → `B00G6D6HBG` | 0.200 | `B09HTM917T` → `B086QQNRT4` → `B09Q4RTSLX` | 0.000 |
| th15 | `B07PTVBKHM` → `B08N6T81FB` | `B07PTVBKHM` → `B08N6T81FB` | 1.000 | `B0BQDTZDP6` → `B007W63WZU` → `B00AFKCBQQ` | 0.000 |
| th16 | `B00G6D6HBG` → `B017TK4H6G` → `B01N24N9TH` | `B01N24N9TH` → `B00G6D6HBG` → `B017TK4H6G` | 1.000 | `B08BH87FPJ` → `B08JBXX49X` → `B08N6T81FB` | 0.000 |
| th17 | — | missing_final_turn | N/A | `B08HH4RQM5` → `B07KLF7G8J` → `B07Q4F8Q99` | N/A |
| th18 | `B06XTT46WQ` → `B012DTEMQ8` → `B012DTDBI8` | missing_final_turn | N/A | `B00G6D6HBG` → `B09832Q4VK` → `B09WYFWH2Y` | 0.000 |
| th19 | `B01N13866H` → `B00G6D6HBG` → `B09XGVPJV2` | `B012DTEMQ8` → `B08N6T81FB` → `B08BH87FPJ` | 0.000 | clarify-lane | N/A |
| th20 | `B0B7B53HM4` → `B09XM8QVKC` → `B012DTDBI8` | missing_final_turn | N/A | clarify-lane | N/A |

## Interpretation boundary

Oracle와의 일치는 state-to-ranking 전달 충실도 진단이다. 인간 relevance, 만족도, Product NDCG 또는 추천 품질 우위를 의미하지 않는다. 이 분석은 포스터 제출 뒤 수행된 post-hoc secondary analysis이며 기존 official primary benchmark를 변경하지 않는다.
