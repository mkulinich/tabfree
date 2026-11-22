# Deploying TabFree on AWS

`infra/terraform/envs/production` deploys everything with one `tofu apply` (BUILD_PLAN P7.5). Before
the first apply:

1. **State.** Create an S3 bucket for state (versioned, encrypted with KMS, public access blocked) and
   fill in the `backend "s3"` block (`use_lockfile = true`). State holds the generated signing keys.
2. **Certificate.** Request an ACM certificate for the site's domain in the primary region.
3. **Mail.** Verify the sender domain in Amazon SES and leave the sandbox (production access).
4. **Identity provider.** Register TabFree as an OIDC client (redirect `https://<domain>/auth/callback`)
   and store the client secret in Secrets Manager; pass its ARN as `oidc_client_secret_arn`.
5. **Image.** Build `infra/docker/api/Dockerfile` (the release workflow builds, signs and attests it),
   push it to the `tabfree/api` ECR repository and pass it **by digest** as `image`.

Then:

```bash
cp infra/terraform/envs/production/terraform.tfvars.example infra/terraform/envs/production/terraform.tfvars
tofu -chdir=infra/terraform/envs/production init
tofu -chdir=infra/terraform/envs/production apply
```

After the first apply, run the database migrations once from a one-off task (the services never
migrate on start):

```bash
aws ecs run-task --cluster tabfree-prod --launch-type FARGATE --task-definition tabfree-prod-worker \
  --overrides '{"containerOverrides":[{"name":"worker","command":["python","-m","tabfree_api.db","upgrade"]}]}' \
  --network-configuration "awsvpcConfiguration={subnets=[<private subnet>],securityGroups=[<app SG>]}"
```

Point the domain at the load balancer (`load_balancer_dns_name`). Create the first site and its
administrator with `python -m tabfree_api.sites create` (same one-off task pattern), then sign in.

**Upgrades:** build and push the new image, change `image`, `tofu apply`. ECS rolls the API with no
downtime and rolls back by itself if the new tasks fail their health checks. Run migrations *before*
switching the image when a release notes "migration required"; migrations are additive within a minor
version.

What the production environment gives you: three AZs with a NAT gateway each; the API on at least two
tasks across AZs, autoscaled on CPU; two workers; Aurora Serverless v2 with a reader for failover,
14 days of point-in-time recovery and Performance Insights; one KMS key for data at rest; WAF with AWS
managed rules and a per-IP rate limit; secrets only in Secrets Manager; daily and monthly backups
copied to the DR region.
