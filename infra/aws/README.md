# AWS environment setup

The deployment system assumes a small set of pre-existing AWS resources. The
worker creates per-deployment artefacts (ECR repo, task def, target group,
listener rule, ECS service, Route53 record) but never the shared backbone —
so the backbone is described here.

## What lives where

| Resource | Owned by | Notes |
|---|---|---|
| VPC + private subnets + NAT | Terraform (you) | `ecs_subnet_ids` |
| Security group "tasks" | Terraform | allows ALB SG → :container_port; egress all |
| Security group "alb" | Terraform | allows 443 from internet |
| Application Load Balancer | Terraform | one shared ALB for all user apps |
| HTTPS listener (:443) | Terraform | with an ACM cert for `*.<apps_domain>` |
| ECS cluster | Terraform | one shared Fargate cluster |
| Task execution role | Terraform | pulls from ECR, writes CW logs |
| Task role | Terraform | empty by default — user containers get no AWS perms |
| CloudWatch log group | Terraform | `/ecs/ai-devops-saas/users` |
| Route53 hosted zone | Terraform | for `<apps_domain>` |
| ECR repository (per deploy) | Worker (`ecr.ensure_repository`) |
| Task definition (per deploy) | Worker (`ecs.register_task_definition`) |
| Target group (per deploy) | Worker (`elbv2.create_target_group`) |
| Listener rule (per deploy) | Worker (`elbv2.create_listener_rule`) |
| ECS service (per deploy) | Worker (`ecs.create_service`) |
| Route53 ALIAS (per deploy) | Worker (`route53.upsert_alias`) |

## One-time setup

### 1. ACM certificate

Issue a wildcard cert covering `*.<apps_domain>` in the **same region** as the
ALB. DNS validation is fastest if the hosted zone already exists. Attach it to
the HTTPS listener. The wildcard means new deployments don't need a per-app
cert.

### 2. ALB + listener

Internet-facing ALB in the public subnets. One HTTPS listener on :443 with the
wildcard cert. Default action: a static fixed-response 404 ("no such app").
The worker adds host-header rules above this default.

### 3. ECS cluster + IAM roles

Create the Fargate cluster. Create two IAM roles:

- **task execution role** — needs `AmazonECSTaskExecutionRolePolicy` plus ECR
  pull (`ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
  `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`) and CloudWatch Logs write.
- **task role** — start with no policies attached. User containers run as
  this; we don't want them able to call AWS by default.

### 4. CloudWatch log group

Create `/ecs/ai-devops-saas/users` with a 14-day retention. The worker logs
into stream prefix = deployment slug.

### 5. Route53 hosted zone

For `<apps_domain>` (e.g. `apps.example.com`). Get its `HostedZoneId`. The
ALB's DNS name + canonical hosted zone are needed too — we use them as the
target of the ALIAS records the worker creates.

### 6. Worker IAM

The IAM principal the worker runs as needs:

```
ecr:CreateRepository, ecr:DescribeRepositories, ecr:DeleteRepository,
ecr:GetAuthorizationToken, ecr:BatchCheckLayerAvailability,
ecr:PutImage, ecr:InitiateLayerUpload, ecr:UploadLayerPart,
ecr:CompleteLayerUpload

ecs:RegisterTaskDefinition, ecs:DeregisterTaskDefinition,
ecs:CreateService, ecs:UpdateService, ecs:DeleteService,
ecs:DescribeServices, iam:PassRole (scoped to the two task roles above)

elasticloadbalancing:CreateTargetGroup, DeleteTargetGroup,
DescribeTargetGroups, DescribeTargetHealth,
CreateRule, DeleteRule, DescribeRules

route53:ChangeResourceRecordSets, route53:ListResourceRecordSets
  (scoped to the hosted zone)

logs:CreateLogStream  (already covered by the task execution role policy
                       it uses; worker doesn't write logs itself)
```

In production, attach this via IRSA (IAM Roles for Service Accounts) if the
worker runs on EKS, or via the task role if it runs on ECS. Don't bake static
credentials into the worker image.

## Environment variables (worker)

Mirror these to `.env` (dev) or your secrets manager (prod). Names match the
`Settings` fields in `shared/shared/core/config.py`.

```
AWS_REGION=us-east-1
AWS_ACCOUNT_ID=123456789012
ECR_NAMESPACE=ai-devops-saas/users

ECS_CLUSTER=ai-devops-saas-prod
ECS_TASK_EXECUTION_ROLE_ARN=arn:aws:iam::123456789012:role/ai-devops-saas-task-exec
ECS_TASK_ROLE_ARN=arn:aws:iam::123456789012:role/ai-devops-saas-task
ECS_SUBNET_IDS=["subnet-aaa","subnet-bbb","subnet-ccc"]
ECS_SECURITY_GROUP_IDS=["sg-tasks"]
ECS_LOG_GROUP=/ecs/ai-devops-saas/users
ECS_TASK_CPU=256
ECS_TASK_MEMORY=512

ALB_ARN=arn:aws:elasticloadbalancing:...:loadbalancer/app/.../...
ALB_HTTPS_LISTENER_ARN=arn:aws:elasticloadbalancing:...:listener/app/.../...
ALB_VPC_ID=vpc-...
ALB_DNS_NAME=internal-foo-1234.us-east-1.elb.amazonaws.com
ALB_HOSTED_ZONE_ID=Z35SXDOTRQ7X7K   # ALB canonical zone, looked up per region

APPS_DOMAIN=apps.example.com
HOSTED_ZONE_ID=Z01234567890ABCDEFGHI

DEPLOY_WAIT_TIMEOUT_SECONDS=600
DEPLOY_WAIT_INTERVAL_SECONDS=10
```

The ALB canonical hosted zone IDs are documented in
[the ALB DNS docs](https://docs.aws.amazon.com/general/latest/gr/elb.html);
they're not the same as your Route53 zone ID.

## Local development

For local dev the worker can run without AWS — `analyze` works (pure GitHub
+ Anthropic), `build` will fail when it tries to push to ECR, and `deploy`
will fail without ECS configured. Use `localstack` or just run only the
analyzer worker pool until the AWS env is wired up:

```bash
celery -A worker.celery_app:app worker -Q analyzer --loglevel=info
```

## Cost notes

- Each deployment creates one Fargate task at 0.25 vCPU / 0.5 GB → ~$9/month
  baseline if always-on.
- ALB itself is ~$16/month flat regardless of the number of rules.
- ECR storage is $0.10/GB-month; we use IMMUTABLE tags so old builds don't
  silently roll over. Add a lifecycle policy if many deployments accumulate.

## Teardown

`DELETE /deployments/{id}` enqueues `worker.tasks.deploy.teardown`, which
deletes the per-deployment resources in reverse-creation order. The shared
backbone is not touched.

If something gets stuck, the row holds the ARNs — manual cleanup via `aws`
CLI is straightforward.
