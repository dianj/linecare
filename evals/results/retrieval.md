# Retrieval: embedding models compared

32 answerable and 9 unanswerable questions (EN and BG) against the public KB; top-4 passages; CPU (ONNX via fastembed).

| Model | hit@1 | hit@4 | answerable top score (min / median) | unanswerable top score (median / max) | best threshold (accuracy) | index build | query |
|---|---|---|---|---|---|---|---|
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 94% | 100% | 0.362 / 0.637 | 0.374 / 0.458 | 0.462 (95%) | 1.9 s | 4 ms |
| `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` | 91% | 100% | 0.455 / 0.664 | 0.337 / 0.532 | 0.45 (95%) | 5.1 s | 17 ms |
| `intfloat/multilingual-e5-large` | 84% | 91% | 0.809 / 0.865 | 0.793 / 0.807 | 0.808 (100%) | 12.3 s | 52 ms |

Top-1 misses for `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`:
- 'How can I pay my bill?': got PUB-08 (0.571), expected ['PUB-09']
- 'Как мога да платя сметката си?': got PUB-08 (0.591), expected ['PUB-09']

Top-1 misses for `sentence-transformers/paraphrase-multilingual-mpnet-base-v2`:
- 'How can I pay my bill?': got PUB-08 (0.621), expected ['PUB-09']
- 'Will I get money back for the outage?': got PUB-08 (0.527), expected ['PUB-06']
- 'Как мога да платя сметката си?': got PUB-08 (0.651), expected ['PUB-09']

Top-1 misses for `intfloat/multilingual-e5-large`:
- 'Как мога да платя сметката си?': got PUB-14-BG (0.809), expected ['PUB-09']
- 'Wi-Fi-ят е бавен, а с кабел е добре': got PUB-14-BG (0.846), expected ['PUB-02']
- 'Спряха ми услугите заради неплатена сметка, какво да направя?': got PUB-14-BG (0.851), expected ['PUB-08']
- 'Какво означава оранжевата светлина на рутера?': got PUB-14-BG (0.865), expected ['PUB-03']
- 'Местя се в друг апартамент, какво трябва да направя?': got PUB-14-BG (0.816), expected ['PUB-11']
