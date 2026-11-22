# TabFree Helm chart

For clusters on EKS (BUILD_PLAN P7.5). The supported AWS deploy is `infra/terraform/envs/production`
(ECS on Fargate); this chart runs the same image and settings on Kubernetes.

What it creates: the API Deployment (with a Service, an HPA on CPU, a PodDisruptionBudget and an
Ingress for the AWS Load Balancer Controller), the job worker (`jobs worker --scheduler --all-sites`),
a pre-install/pre-upgrade Job that migrates the database, a ServiceAccount for IRSA, and NetworkPolicies.

What it expects to exist:

- Aurora PostgreSQL (`modules/metadata_db`), an ACM certificate and, ideally, a WAF web ACL;
- an IAM role for the service account with the task role's permissions (`modules/app`);
- a Secret (default name `tabfree`) with the keys in `values.yaml` → `secrets.env`. Create it with
  External Secrets or the Secrets Store CSI driver from Secrets Manager; the chart never takes secret
  values.

```bash
helm install tabfree infra/helm/tabfree -n tabfree --create-namespace \
  --set image.digest=sha256:... \
  --set config.TABFREE_BASE_URL=https://tabfree.corp.example \
  --set 'ingress.hosts={tabfree.corp.example}' \
  --set serviceAccount.annotations.eks\.amazonaws\.com/role-arn=arn:aws:iam::123456789012:role/tabfree
```

Pods run as user 10001 with a read-only root filesystem (an `emptyDir` at `/tmp`), no privilege
escalation and all capabilities dropped.

> **Status:** written without `helm` or a cluster available, so it has **not been rendered or
> installed**. Run `helm lint` and `helm template` before first use.
