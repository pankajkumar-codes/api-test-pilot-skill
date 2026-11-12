# Load, stress, spike, soak (stub — build in Phase 3)

Safety gate applies: non-production target and confirmed authorization first.

TODO: generate k6 scripts for each profile with thresholds
(p95 latency, error rate), ramp stages, and result summaries.

| Profile | Shape | Finds |
|---------|-------|-------|
| Load | expected traffic, steady | latency/error baseline |
| Stress | ramp past capacity | breaking point, recovery |
| Spike | sudden surge | autoscaling, queueing |
| Soak | long steady run | leaks, degradation |
