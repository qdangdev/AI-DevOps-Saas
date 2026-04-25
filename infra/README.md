# infra/

Deployment & cloud configuration. Anything that *describes the environment*
rather than the application itself lives here.

```
infra/
  compose/          # docker-compose overlays for local dev / staging
  terraform/        # AWS infra: VPC, ECS clusters, RDS, ElastiCache, ECR
  ecs/              # ECS task definitions per service (api, worker-*, user-app template)
  github-actions/   # reusable workflow snippets pulled into .github/workflows
```

The root `docker-compose.yml` covers happy-path local dev. `infra/compose/`
holds opt-in overlays (e.g. `compose.observability.yml` to add Prometheus +
Grafana when debugging).

`terraform/` is the source of truth for the AWS account; CI applies it from
the `main` branch only.
