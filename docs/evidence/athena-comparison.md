# CSV vs Parquet: Athena bytes scanned

Measured 2026-10-25 04:18 UTC in workgroup `etl-pipeline-dev`, database `etl_pipeline_dev`, on the **same 950 rows**: the clean output of job `5e94a237b6d6e354`, read once from its `processed/` CSV and once from its `curated/` snappy Parquet (temporary tables over exactly those two folders). Produced by `scripts/compare_formats.py`.

| Query | CSV scanned | Parquet scanned | Reduction | CSV time | Parquet time |
|---|---:|---:|---:|---:|---:|
| Row count | 51,520 B | 0 B | 100.0% | 723 ms | 505 ms |
| Monthly revenue | 51,520 B | 6,839 B | 86.7% | 530 ms | 495 ms |
| Top 10 categories | 51,520 B | 6,391 B | 87.6% | 559 ms | 940 ms |
| Region by month | 51,520 B | 7,312 B | 85.8% | 505 ms | 614 ms |
| Full row scan | 51,520 B | 19,971 B | 61.2% | 537 ms | 525 ms |

**Total:** 257,600 B (CSV) vs 40,513 B (Parquet), **84.3% less data scanned**.

Parquet is columnar: a query reads only the columns it uses, and `COUNT(*)` can be answered from Parquet metadata alone. CSV must be read in full every time. Athena bills per byte scanned (10 MB minimum per query), so at this tiny scale both cost fractions of a cent; the ratio is what carries over to real volumes.
