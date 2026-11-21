# Extract converter (BUILD_PLAN P2.8): an ECS Fargate task definition that runs
# `tabfree-migrate convert-batch`, and least-privilege IAM roles for it.
#
# Nothing runs until someone calls RunTask, so this module costs nothing at rest.
# Fan out by starting N tasks with `--shard i/N` (see the module README).
#
# The task role can:
#   - read the raw zone's content-addressed store (blobs/, downloads/ by default),
#   - write the converter's output prefix and the Glue database's warehouse prefix in the lake,
#   - create, update and drop tables in one Glue database (staging table, Iceberg CTAS, parameters),
#   - run Athena queries in one workgroup and use its results prefix.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region
  partition  = data.aws_partition.current.partition

  raw_prefixes   = [for p in var.raw_read_prefixes : trimsuffix(p, "/")]
  lake_prefixes  = [var.output_prefix, "warehouse/${var.glue_database_name}"]
  lake_writeable = [for p in local.lake_prefixes : trimsuffix(p, "/")]

  glue_arns = [
    "arn:${local.partition}:glue:${local.region}:${local.account_id}:catalog",
    "arn:${local.partition}:glue:${local.region}:${local.account_id}:database/${var.glue_database_name}",
    "arn:${local.partition}:glue:${local.region}:${local.account_id}:table/${var.glue_database_name}/*",
  ]

  container_name = "converter"
  scratch_path   = "/scratch"
}

# --- Task role: what the converter itself may do ---------------------------------

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${var.name}-converter-task"
  description        = "TabFree extract converter: read raw extracts, write the lake, register tables."
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "ListRawPrefixes"
    actions   = ["s3:ListBucket"]
    resources = [var.raw_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in local.raw_prefixes : "${p}/*"]
    }
  }

  statement {
    sid       = "ReadRawExtracts"
    actions   = ["s3:GetObject"]
    resources = [for p in local.raw_prefixes : "${var.raw_bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "ListLakePrefixes"
    actions   = ["s3:ListBucket"]
    resources = [var.lake_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in local.lake_writeable : "${p}/*"]
    }
  }

  # Parquet, reports and manifests under output_prefix; Athena's CTAS writes Iceberg data and
  # metadata under the warehouse prefix with these same credentials. No DeleteObject.
  statement {
    sid       = "WriteLakePrefixes"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [for p in local.lake_writeable : "${var.lake_bucket_arn}/${p}/*"]
  }

  statement {
    sid       = "AthenaResults"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [var.athena_results_bucket_arn]
  }

  statement {
    sid       = "AthenaResultObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${var.athena_results_bucket_arn}/${trimsuffix(var.athena_results_prefix, "/")}/*"]
  }

  statement {
    sid = "GlueTablesInOneDatabase"
    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartitions",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
    ]
    resources = local.glue_arns
  }

  statement {
    sid = "AthenaInOneWorkgroup"
    actions = [
      "athena:GetWorkGroup",
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]
    resources = [var.athena_workgroup_arn]
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [var.kms_key_arn]

    content {
      sid       = "UseLakeKey"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [statement.value]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "converter"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# --- Execution role: what ECS needs to start the task (pull image, write logs) ----

resource "aws_iam_role" "execution" {
  name               = "${var.name}-converter-execution"
  description        = "ECS agent for the TabFree extract converter: pull its image, write its logs."
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

data "aws_iam_policy_document" "execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level permissions
  }

  statement {
    sid       = "PullConverterImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [var.ecr_repository_arn]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${var.log_group_arn}:log-stream:*"]
  }
}

resource "aws_iam_role_policy" "execution" {
  name   = "converter-execution"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# --- Task definition -------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.name}-converter"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # the Hyper API has no Linux arm64 build
  }

  ephemeral_storage {
    size_in_gib = var.ephemeral_storage_gib
  }

  # Fargate bind mount on the task's ephemeral storage: the only writable path.
  volume {
    name = "scratch"
  }

  container_definitions = jsonencode([
    {
      name                   = local.container_name
      image                  = var.image
      essential              = true
      user                   = "10001:10001"
      readonlyRootFilesystem = true
      command                = ["convert-batch", "--workers", tostring(var.workers)]
      environment = [
        { name = "AWS_REGION", value = local.region },
        { name = "TABFREE_CONVERT_STORE", value = var.store_location },
        { name = "TABFREE_CONVERT_OUT", value = var.output_location },
        { name = "TABFREE_GLUE_DATABASE", value = var.glue_database_name },
        { name = "TABFREE_ICEBERG_ROOT", value = var.iceberg_root },
        { name = "TABFREE_ATHENA_WORKGROUP", value = var.athena_workgroup_name },
        { name = "TABFREE_WORK_DIR", value = "${local.scratch_path}/work" },
        { name = "TMPDIR", value = local.scratch_path },
      ]
      mountPoints = [
        { sourceVolume = "scratch", containerPath = local.scratch_path, readOnly = false },
      ]
      linuxParameters = {
        initProcessEnabled = true # reap hyperd processes
        capabilities       = { drop = ["ALL"] }
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.log_group_name
          awslogs-region        = local.region
          awslogs-stream-prefix = local.container_name
        }
      }
    },
  ])
}
