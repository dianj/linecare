# Retrieval: embedding models compared

24 answerable and 9 unanswerable questions (EN and BG) against the public KB; top-4 passages; CPU (ONNX via fastembed).

| Model | hit@1 | hit@4 | answerable top score (min / median) | unanswerable top score (median / max) | best threshold (accuracy) | index build | query |
|---|---|---|---|---|---|---|---|
| `intfloat/multilingual-e5-large` | 83% | 92% | 0.809 / 0.865 | 0.792 / 0.813 | 0.807 (97%) | 5.3 s | 35 ms |
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 88% | 100% | 0.455 / 0.670 | 0.290 / 0.520 | 0.446 (97%) | 2.2 s | 10 ms |
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 92% | 100% | 0.467 / 0.653 | 0.328 / 0.458 | 0.462 (100%) | 1.0 s | 3 ms |

Top-1 misses for `intfloat/multilingual-e5-large`:
- 'Как мога да платя сметката си?': got PUB-14-BG (0.809), expected ['PUB-09']
- 'Wi-Fi-ят е бавен, а с кабел е добре': got PUB-14-BG (0.846), expected ['PUB-02']
- 'Спряха ми услугите заради неплатена сметка, какво да направя?': got PUB-14-BG (0.851), expected ['PUB-08']
- 'Какво означава оранжевата светлина на рутера?': got PUB-14-BG (0.865), expected ['PUB-03']

Top-1 misses for `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`:
- 'How can I pay my bill?': got PUB-08 (0.621), expected ['PUB-09']
- 'Will I get money back for the outage?': got PUB-08 (0.527), expected ['PUB-06']
- 'Как мога да платя сметката си?': got PUB-08 (0.651), expected ['PUB-09']

Top-1 misses for `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`:
- 'How can I pay my bill?': got PUB-08 (0.571), expected ['PUB-09']
- 'Как мога да платя сметката си?': got PUB-08 (0.591), expected ['PUB-09']
