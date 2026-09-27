# Deploying on AWS App Runner

One always-on App Runner service in `ap-northeast-1`, fed from ECR, serving
megane's demo Builder in public mode: no token, and only requests whose
`Origin` is one of `allowed_origins` are answered. Terraform (`deploy/terraform/`) keeps its
state in the `megane-terraform-state` bucket megane's demo stack already uses,
under the key `builder-tools/terraform.tfstate`.

```
megane Builder page ──HTTPS + Origin──▶ App Runner (1 instance, 2 vCPU / 4 GB)
 (megane.tech-office-mori.com,            └─ megane-builder-tools --transport http
  megane-labs.github.io)                       /mcp (Streamable HTTP, stateless)
                                               /health (App Runner health check)
```

## Limits that matter

- **App Runner closes every request after 120 seconds** (not configurable). A
  tool call is one request, so the server stops a call at
  `MEGANE_BUILDER_TOOLS_CALL_TIMEOUT` (100 s by default) and returns a tool
  error the Builder shows ("did not finish within the server's 100 s limit;
  try a smaller system"). Rough guide on 2 vCPU: a 100-molecule water box
  takes about a second, a 100-unit polyethylene chain about 40 s; 200 units
  (about 7 minutes) cannot run here.
- **Two calls compute at once** (`MEGANE_BUILDER_TOOLS_MAX_CONCURRENCY`, about
  one per vCPU); a third waits for a free slot and fails with "server is busy"
  if none frees up within the time limit. A call that ran out of time keeps its
  slot until its computation really ends, so an overrun cannot pile up work.
- Images are x86_64 (`--platform linux/amd64`); App Runner runs no ARM images.

## First deployment

Prerequisites: the AWS credentials megane's deploy workflow uses (repository
secrets `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in this repository too),
permission to manage App Runner, ECR and IAM roles.

**From GitHub Actions:** run *Deploy to AWS App Runner* (Actions → workflow
dispatch). It creates the ECR repository, builds and pushes the image tagged
with the commit SHA, applies Terraform, starts an App Runner deployment and
waits for it to succeed, checks `/health` and that a request
without an allowed `Origin` is refused, runs the conformance checker against
the live endpoint and makes one real `liquid_box` call. Later pushes to `main` that
touch the server redeploy automatically.

**By hand:**

```bash
cd deploy/terraform
terraform init
TAG=$(git rev-parse HEAD)
terraform apply -target=aws_ecr_repository.tools -var image_tag=$TAG
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:$TAG" ../.. && docker push "$REPO:$TAG"
terraform apply -var image_tag=$TAG
aws apprunner start-deployment --service-arn "$(terraform output -raw service_arn)"
terraform output mcp_endpoint
```

## Using it from megane Builder

```bash
cd deploy/terraform
terraform output -raw mcp_endpoint    # https://xxxx.ap-northeast-1.awsapprunner.com/mcp
```

megane's demo site is built with this endpoint as Builder's default tool
server, so its *Python tools* section connects by itself. Any other Builder
page listed in `allowed_origins` can add the endpoint in *Python tools* (no
token) or open `builder.html#tools=<endpoint>`. To use a local Builder, add
`http://localhost:5173` to `allowed_origins` (or `terraform.tfvars`).

**Public mode is not authentication.** A script can send an allowed `Origin`
header; it keeps crawlers and other sites' pages out, while the 100 s call
limit and two concurrent calls bound the damage (at worst the demo answers
"server is busy"; the single fixed instance keeps the bill flat). LLM clients
connect by sending an allowed `Origin` header. For a private server, run
without `MEGANE_BUILDER_TOOLS_PUBLIC` and set `MEGANE_BUILDER_TOOLS_TOKEN`
(see the top-level README).

## Cost

One provisioned instance is billed for its memory all the time and for its
vCPUs only while it handles requests. At the ap-northeast-1 rates
(US$0.009 per GB-hour, US$0.081 per vCPU-hour, checked 2026-09):

| Size | Idle, per month (730 h) | Added per hour of computing |
| --- | --- | --- |
| 2 vCPU / 4 GB (default) | 4 × 0.009 × 730 ≈ US$26 | 2 × 0.081 ≈ US$0.16 |
| 1 vCPU / 2 GB (`cpu = "1024"`, `memory = "2048"`) | 2 × 0.009 × 730 ≈ US$13 | ≈ US$0.08 |

ECR storage adds a few cents. See
the [App Runner pricing page](https://aws.amazon.com/apprunner/pricing/) for
current rates.

## Tear down

```bash
cd deploy/terraform && terraform destroy -var image_tag=unused
```
