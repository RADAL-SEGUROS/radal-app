#!/usr/bin/env bash
# v12 egress for the VPC-attached backend Lambda: a fck-nat instance (t4g.nano)
# in a public subnet, behind a STATIC ENI that private subnets route 0.0.0.0/0 to.
# See deploy/v12/README.md (§1, and the open egress question) and docs/v12/08-infra.md §1.
#
#   DRY_RUN=1 ./deploy/v12/fck-nat.sh                 # default: print only, no AWS call
#   DRY_RUN=0 PUBLIC_SUBNET_ID=subnet-… PRIVATE_SUBNET_CIDRS="172.31.96.0/24 172.31.97.0/24" \
#       ./deploy/v12/fck-nat.sh                        # apply (idempotent)
#   … SWITCH_LAMBDA_SUBNETS=1 …                        # also move the Lambda onto the private subnets
#
# What it builds (each step describe-before-create):
#   1. SG  <prefix>-sg      : all traffic IN from the Lambda SG only; egress all. No SSH.
#   2. ENI <prefix>-eni     : static, in PUBLIC_SUBNET_ID, source/dest check OFF — the route target.
#   3. EIP <prefix>-eip     : stable egress IP (Forpay/HDI/Zoho allow-lists) — ALLOCATE_EIP=1.
#   4. IAM <prefix>-role + instance profile: attach the ENI / associate the EIP at boot; SSM access.
#   5. Launch template + ASG of exactly 1 (self-healing): user-data writes /etc/fck-nat.conf.
#   6. Private route table 0.0.0.0/0 -> ENI (new subnets, or an existing private RT).
#   7. S3 gateway endpoint on that route table (free; keeps S3 off the NAT).
#   8. (opt-in) Lambda VpcConfig -> the private subnets. Everything before this step is inert.
#
# Rollback: README.md §1 "Rollback". Swapping to a NAT Gateway later = one replace-route.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------- variables
NAME_PREFIX="${NAME_PREFIX:-radal-dev-nat}"
LAMBDA_FUNCTION="${LAMBDA_FUNCTION:-radal-backend-dev}"
LAMBDA_SG_ID="${LAMBDA_SG_ID:-sg-065438bbc14858097}"     # radal-lambda-sg (RDS allows it on 3306)
VPC_ID="${VPC_ID:-}"                                       # blank -> read from the Lambda's VpcConfig
PUBLIC_SUBNET_ID="${PUBLIC_SUBNET_ID:-}"                   # REQUIRED to apply: its RT must have 0.0.0.0/0 -> igw-*
PRIVATE_ROUTE_TABLE_ID="${PRIVATE_ROUTE_TABLE_ID:-}"       # use an EXISTING private RT (refused if it has an igw default)
PRIVATE_SUBNET_CIDRS="${PRIVATE_SUBNET_CIDRS:-}"           # else create these subnets, e.g. "172.31.96.0/24 172.31.97.0/24"
PRIVATE_SUBNET_AZS="${PRIVATE_SUBNET_AZS:-us-east-1a us-east-1b}"   # one AZ per CIDR, same order
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.nano}"
FCK_NAT_AMI_ID="${FCK_NAT_AMI_ID:-}"                       # blank -> newest public fck-nat AL2023 arm64 AMI
FCK_NAT_AMI_OWNER="${FCK_NAT_AMI_OWNER:-568608671756}"     # the fck-nat project's AMI publisher
FCK_NAT_AMI_NAME="${FCK_NAT_AMI_NAME:-fck-nat-al2023-*-arm64-ebs}"
ALLOCATE_EIP="${ALLOCATE_EIP:-1}"
CREATE_S3_ENDPOINT="${CREATE_S3_ENDPOINT:-1}"
REPLACE_DEFAULT_ROUTE="${REPLACE_DEFAULT_ROUTE:-0}"        # 1 = repoint an existing non-igw default route at the ENI
SWITCH_LAMBDA_SUBNETS="${SWITCH_LAMBDA_SUBNETS:-0}"        # 1 = update the Lambda VpcConfig (the only user-visible step)
LAMBDA_SUBNET_IDS="${LAMBDA_SUBNET_IDS:-}"                 # comma list for step 8; blank -> the subnets created in step 6

# shellcheck source=deploy/v12/_lib.sh
. "$HERE/_lib.sh"

SG_NAME="$NAME_PREFIX-sg"
ENI_NAME="$NAME_PREFIX-eni"
EIP_NAME="$NAME_PREFIX-eip"
ROLE_NAME="$NAME_PREFIX-role"
LT_NAME="$NAME_PREFIX-lt"
ASG_NAME="$NAME_PREFIX-asg"
RT_NAME="$NAME_PREFIX-private-rt"

preflight
if ! is_dry && [ -z "$PUBLIC_SUBNET_ID" ]; then
  die "PUBLIC_SUBNET_ID is required (a subnet of the Lambda's VPC whose route table sends 0.0.0.0/0 to an igw-*)"
fi
if ! is_dry && [ -z "$PRIVATE_ROUTE_TABLE_ID" ] && [ -z "$PRIVATE_SUBNET_CIDRS" ]; then
  die "set PRIVATE_ROUTE_TABLE_ID (existing private RT) or PRIVATE_SUBNET_CIDRS (new private subnets)"
fi

# Route table that governs a subnet (explicit association, else the VPC main table).
route_table_for_subnet() {
  local subnet="$1" rt
  rt=$(aws_read ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$subnet" \
        --query 'RouteTables[0].RouteTableId')
  if [ -z "$rt" ]; then
    rt=$(aws_read ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" "Name=association.main,Values=true" \
          --query 'RouteTables[0].RouteTableId')
  fi
  printf '%s' "$rt"
}

# ---------------------------------------------------------------- 0. where are we
log "0. Lambda VPC"
if [ -z "$VPC_ID" ]; then
  VPC_ID=$(aws_read lambda get-function-configuration --function-name "$LAMBDA_FUNCTION" --query 'VpcConfig.VpcId')
fi
VPC_ID=$(or_placeholder "$VPC_ID" "<VPC_ID>")
CURRENT_SUBNETS=$(aws_read lambda get-function-configuration --function-name "$LAMBDA_FUNCTION" --query 'VpcConfig.SubnetIds')
note "VPC: $VPC_ID · current Lambda subnets: $(or_placeholder "$CURRENT_SUBNETS" '<read in apply mode>')"
if ! is_dry && [ "$VPC_ID" = "<VPC_ID>" ]; then
  die "the Lambda $LAMBDA_FUNCTION has no VpcConfig — it is NOT VPC-attached, so it needs no NAT (see README open question)"
fi
LAMBDA_SG_EGRESS=$(aws_read ec2 describe-security-groups --group-ids "$LAMBDA_SG_ID" \
  --query "SecurityGroups[0].IpPermissionsEgress[?IpProtocol=='-1'].IpRanges[].CidrIp")
if ! is_dry && ! printf '%s' "$LAMBDA_SG_EGRESS" | tr '\t' '\n' | grep -qx '0.0.0.0/0'; then
  warn "$LAMBDA_SG_ID does not allow all egress to 0.0.0.0/0 ('$LAMBDA_SG_EGRESS'). SMTP 465/587, IMAP 993 and HTTPS 443 must be open."
fi

PUBLIC_SUBNET_ID=$(or_placeholder "$PUBLIC_SUBNET_ID" "<PUBLIC_SUBNET_ID>")
PUB_RT=$(route_table_for_subnet "$PUBLIC_SUBNET_ID")
PUB_DEFAULT=$(aws_read ec2 describe-route-tables --route-table-ids "$(or_placeholder "$PUB_RT" '<PUBLIC_RT>')" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'].GatewayId | [0]")
if ! is_dry; then
  case "$PUB_DEFAULT" in
    igw-*) note "public subnet $PUBLIC_SUBNET_ID routes 0.0.0.0/0 -> $PUB_DEFAULT (ok)";;
    *) die "PUBLIC_SUBNET_ID $PUBLIC_SUBNET_ID has no 0.0.0.0/0 -> igw-* route (got '$PUB_DEFAULT')";;
  esac
fi

# ---------------------------------------------------------------- 1. security group
log "1. NAT security group $SG_NAME (in: Lambda SG only)"
SG_ID=$(aws_read ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId')
if [ -z "$SG_ID" ]; then
  SG_ID=$(aws_do "<NAT_SG_ID>" ec2 create-security-group --group-name "$SG_NAME" --vpc-id "$VPC_ID" \
    --description "Radal fck-nat: traffic from radal-lambda-sg only" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Name,Value=$SG_NAME}]" --query GroupId)
else
  note "exists: $SG_ID"
fi
HAS_RULE=$(aws_read ec2 describe-security-groups --group-ids "$SG_ID" \
  --query "SecurityGroups[0].IpPermissions[?IpProtocol=='-1'].UserIdGroupPairs[].GroupId")
HAS_RULE=$(printf '%s' "$HAS_RULE" | tr '\t' ' ')
case " $HAS_RULE " in
  *" $LAMBDA_SG_ID "*) note "ingress from $LAMBDA_SG_ID already present";;
  *) aws_run ec2 authorize-security-group-ingress --group-id "$SG_ID" \
       --ip-permissions "IpProtocol=-1,UserIdGroupPairs=[{GroupId=$LAMBDA_SG_ID,Description=radal-lambda-sg}]";;
esac

# ---------------------------------------------------------------- 2. static ENI
log "2. static ENI $ENI_NAME (the private route target)"
ENI_ID=$(aws_read ec2 describe-network-interfaces --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=$ENI_NAME" \
  --query 'NetworkInterfaces[0].NetworkInterfaceId')
if [ -z "$ENI_ID" ]; then
  ENI_ID=$(aws_do "<NAT_ENI_ID>" ec2 create-network-interface --subnet-id "$PUBLIC_SUBNET_ID" --groups "$SG_ID" \
    --description "Radal fck-nat static ENI" \
    --tag-specifications "ResourceType=network-interface,Tags=[{Key=Name,Value=$ENI_NAME}]" \
    --query 'NetworkInterface.NetworkInterfaceId')
else
  note "exists: $ENI_ID"
fi
aws_run ec2 modify-network-interface-attribute --network-interface-id "$ENI_ID" --no-source-dest-check

# ---------------------------------------------------------------- 3. elastic IP
EIP_ALLOC=""
if [ "$ALLOCATE_EIP" = "1" ]; then
  log "3. Elastic IP $EIP_NAME (stable egress address for allow-lists)"
  EIP_ALLOC=$(aws_read ec2 describe-addresses --filters "Name=tag:Name,Values=$EIP_NAME" --query 'Addresses[0].AllocationId')
  if [ -z "$EIP_ALLOC" ]; then
    EIP_ALLOC=$(aws_do "<EIP_ALLOCATION_ID>" ec2 allocate-address --domain vpc \
      --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$EIP_NAME}]" --query AllocationId)
  else
    note "exists: $EIP_ALLOC"
  fi
else
  log "3. Elastic IP skipped (ALLOCATE_EIP=0): egress IP changes whenever the instance is replaced"
fi

# ---------------------------------------------------------------- 4. IAM
log "4. instance role + profile $ROLE_NAME"
ROLE_ARN=$(aws_read iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn')
if [ -z "$ROLE_ARN" ]; then
  aws_run iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
else
  note "exists: $ROLE_ARN"
fi
aws_run iam put-role-policy --role-name "$ROLE_NAME" --policy-name fck-nat-ha \
  --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ec2:AttachNetworkInterface","ec2:ModifyNetworkInterfaceAttribute","ec2:AssociateAddress","ec2:DisassociateAddress","ec2:DescribeNetworkInterfaces","ec2:DescribeAddresses"],"Resource":"*"}]}'
aws_run iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
PROFILE_ARN=$(aws_read iam get-instance-profile --instance-profile-name "$ROLE_NAME" --query 'InstanceProfile.Arn')
if [ -z "$PROFILE_ARN" ]; then
  aws_run iam create-instance-profile --instance-profile-name "$ROLE_NAME"
fi
IN_PROFILE=$(aws_read iam get-instance-profile --instance-profile-name "$ROLE_NAME" --query 'InstanceProfile.Roles[].RoleName')
case "$IN_PROFILE" in
  *"$ROLE_NAME"*) note "role already in the instance profile";;
  *) aws_run iam add-role-to-instance-profile --instance-profile-name "$ROLE_NAME" --role-name "$ROLE_NAME"
     if ! is_dry; then note "waiting 15 s for IAM propagation"; sleep 15; fi;;
esac

# ---------------------------------------------------------------- 5. launch template + ASG(1)
log "5. launch template $LT_NAME + ASG $ASG_NAME (min = max = 1)"
if [ -z "$FCK_NAT_AMI_ID" ]; then
  FCK_NAT_AMI_ID=$(aws_read ec2 describe-images --owners "$FCK_NAT_AMI_OWNER" \
    --filters "Name=name,Values=$FCK_NAT_AMI_NAME" "Name=architecture,Values=arm64" "Name=state,Values=available" \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId')
fi
FCK_NAT_AMI_ID=$(or_placeholder "$FCK_NAT_AMI_ID" "<FCK_NAT_AMI_ID>")
if ! is_dry && [ "$FCK_NAT_AMI_ID" = "<FCK_NAT_AMI_ID>" ]; then die "no fck-nat AMI found ($FCK_NAT_AMI_OWNER / $FCK_NAT_AMI_NAME)"; fi
note "AMI: $FCK_NAT_AMI_ID"

# fck-nat HA mode: the service attaches the static ENI (and the EIP) at boot.
USER_DATA="#!/bin/bash
echo \"eni_id=$ENI_ID\" >> /etc/fck-nat.conf"
if [ -n "$EIP_ALLOC" ]; then
  USER_DATA="$USER_DATA
echo \"eip_id=$EIP_ALLOC\" >> /etc/fck-nat.conf"
fi
USER_DATA="$USER_DATA
service fck-nat restart"
USER_DATA_B64=$(printf '%s\n' "$USER_DATA" | base64 | tr -d '\n')
LT_DATA="{\"ImageId\":\"$FCK_NAT_AMI_ID\",\"InstanceType\":\"$INSTANCE_TYPE\",\"IamInstanceProfile\":{\"Name\":\"$ROLE_NAME\"},\"NetworkInterfaces\":[{\"DeviceIndex\":0,\"AssociatePublicIpAddress\":true,\"Groups\":[\"$SG_ID\"],\"DeleteOnTermination\":true}],\"MetadataOptions\":{\"HttpTokens\":\"required\"},\"UserData\":\"$USER_DATA_B64\",\"TagSpecifications\":[{\"ResourceType\":\"instance\",\"Tags\":[{\"Key\":\"Name\",\"Value\":\"$NAME_PREFIX\"}]}]}"

LT_ID=$(aws_read ec2 describe-launch-templates --launch-template-names "$LT_NAME" --query 'LaunchTemplates[0].LaunchTemplateId')
if [ -z "$LT_ID" ]; then
  aws_run ec2 create-launch-template --launch-template-name "$LT_NAME" --launch-template-data "$LT_DATA"
else
  note "exists: $LT_ID — to roll a new AMI: aws ec2 create-launch-template-version --launch-template-name $LT_NAME --source-version '\$Latest' --launch-template-data '{\"ImageId\":\"ami-…\"}', then start-instance-refresh on $ASG_NAME"
fi
ASG_FOUND=$(aws_read autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_NAME" \
  --query 'AutoScalingGroups[0].AutoScalingGroupName')
if [ -z "$ASG_FOUND" ]; then
  aws_run autoscaling create-auto-scaling-group --auto-scaling-group-name "$ASG_NAME" \
    --launch-template "LaunchTemplateName=$LT_NAME,Version=\$Latest" \
    --min-size 1 --max-size 1 --desired-capacity 1 --vpc-zone-identifier "$PUBLIC_SUBNET_ID" \
    --tags "Key=Name,Value=$NAME_PREFIX,PropagateAtLaunch=true"
else
  note "exists: $ASG_FOUND"
fi

# ---------------------------------------------------------------- 6. private routing
log "6. private route 0.0.0.0/0 -> $ENI_ID"
PRIVATE_SUBNET_IDS=""
if [ -n "$PRIVATE_ROUTE_TABLE_ID" ]; then
  RT_ID="$PRIVATE_ROUTE_TABLE_ID"
  note "using the existing route table $RT_ID"
else
  RT_ID=$(aws_read ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=$RT_NAME" \
    --query 'RouteTables[0].RouteTableId')
  if [ -z "$RT_ID" ]; then
    RT_ID=$(aws_do "<PRIVATE_RT_ID>" ec2 create-route-table --vpc-id "$VPC_ID" \
      --tag-specifications "ResourceType=route-table,Tags=[{Key=Name,Value=$RT_NAME}]" --query 'RouteTable.RouteTableId')
  else
    note "exists: $RT_ID"
  fi
fi

DEFAULT_ROUTE=$(aws_read ec2 describe-route-tables --route-table-ids "$RT_ID" \
  --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'] | [0].[GatewayId,NetworkInterfaceId,NatGatewayId,InstanceId]")
DEFAULT_ROUTE=$(printf '%s' "$DEFAULT_ROUTE" | tr '\t' ' ')
case "$DEFAULT_ROUTE" in
  "") aws_run ec2 create-route --route-table-id "$RT_ID" --destination-cidr-block 0.0.0.0/0 --network-interface-id "$ENI_ID";;
  *igw-*) die "route table $RT_ID sends 0.0.0.0/0 to an internet gateway — it is PUBLIC. Refusing to touch it.";;
  *"$ENI_ID"*) note "default route already targets $ENI_ID";;
  *) if [ "$REPLACE_DEFAULT_ROUTE" = "1" ]; then
       aws_run ec2 replace-route --route-table-id "$RT_ID" --destination-cidr-block 0.0.0.0/0 --network-interface-id "$ENI_ID"
     else
       die "route table $RT_ID already has a default route ($DEFAULT_ROUTE). Set REPLACE_DEFAULT_ROUTE=1 to repoint it."
     fi;;
esac

if [ -z "$PRIVATE_ROUTE_TABLE_ID" ]; then
  # shellcheck disable=SC2086
  set -- $PRIVATE_SUBNET_AZS
  i=0
  CIDRS="${PRIVATE_SUBNET_CIDRS:-<CIDR_A> <CIDR_B>}"
  for CIDR in $CIDRS; do
    i=$((i + 1))
    AZ="${1:-}"; if [ $# -gt 0 ]; then shift; fi
    [ -n "$AZ" ] || die "PRIVATE_SUBNET_AZS has fewer AZs than PRIVATE_SUBNET_CIDRS"
    SUBNET_ID=$(aws_read ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=cidr-block,Values=$CIDR" \
      --query 'Subnets[0].SubnetId')
    if [ -z "$SUBNET_ID" ]; then
      SUBNET_ID=$(aws_do "<PRIVATE_SUBNET_$i>" ec2 create-subnet --vpc-id "$VPC_ID" --cidr-block "$CIDR" \
        --availability-zone "$AZ" \
        --tag-specifications "ResourceType=subnet,Tags=[{Key=Name,Value=$NAME_PREFIX-private-$AZ}]" \
        --query 'Subnet.SubnetId')
    else
      note "subnet $CIDR exists: $SUBNET_ID"
    fi
    ASSOC=$(aws_read ec2 describe-route-tables --filters "Name=association.subnet-id,Values=$SUBNET_ID" \
      --query 'RouteTables[0].RouteTableId')
    if [ "$ASSOC" = "$RT_ID" ]; then
      note "$SUBNET_ID already associated with $RT_ID"
    elif [ -n "$ASSOC" ]; then
      die "$SUBNET_ID is explicitly associated with another route table ($ASSOC) — resolve by hand"
    else
      aws_run ec2 associate-route-table --route-table-id "$RT_ID" --subnet-id "$SUBNET_ID"
    fi
    PRIVATE_SUBNET_IDS="${PRIVATE_SUBNET_IDS:+$PRIVATE_SUBNET_IDS,}$SUBNET_ID"
  done
fi

# ---------------------------------------------------------------- 7. S3 gateway endpoint
log "7. S3 gateway endpoint on $RT_ID (free; S3 stays off the NAT)"
S3_SERVICE="com.amazonaws.$REGION.s3"
VPCE_ID=$(aws_read ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=$S3_SERVICE" \
  "Name=vpc-endpoint-type,Values=Gateway" --query 'VpcEndpoints[0].VpcEndpointId')
if [ -n "$VPCE_ID" ]; then
  VPCE_RTS=$(aws_read ec2 describe-vpc-endpoints --vpc-endpoint-ids "$VPCE_ID" --query 'VpcEndpoints[0].RouteTableIds')
  case "$VPCE_RTS" in
    *"$RT_ID"*) note "$VPCE_ID already serves $RT_ID";;
    *) aws_run ec2 modify-vpc-endpoint --vpc-endpoint-id "$VPCE_ID" --add-route-table-ids "$RT_ID";;
  esac
elif [ "$CREATE_S3_ENDPOINT" = "1" ]; then
  aws_run ec2 create-vpc-endpoint --vpc-id "$VPC_ID" --vpc-endpoint-type Gateway --service-name "$S3_SERVICE" \
    --route-table-ids "$RT_ID"
else
  note "no S3 gateway endpoint and CREATE_S3_ENDPOINT=0: S3 traffic will cross the NAT"
fi

# ---------------------------------------------------------------- 8. move the Lambda (opt-in)
LAMBDA_SUBNETS="${LAMBDA_SUBNET_IDS:-$PRIVATE_SUBNET_IDS}"
if [ -z "$LAMBDA_SUBNETS" ]; then
  if ! is_dry && [ "$SWITCH_LAMBDA_SUBNETS" = "1" ]; then
    die "SWITCH_LAMBDA_SUBNETS=1 needs LAMBDA_SUBNET_IDS (subnets associated with $RT_ID, >= 2 AZs)"
  fi
  LAMBDA_SUBNETS="<LAMBDA_SUBNET_IDS associated with $RT_ID>"
fi
VPC_CONFIG="SubnetIds=$LAMBDA_SUBNETS,SecurityGroupIds=$LAMBDA_SG_ID"
if [ "$SWITCH_LAMBDA_SUBNETS" = "1" ]; then
  log "8. Lambda $LAMBDA_FUNCTION -> private subnets ($LAMBDA_SUBNETS)"
  aws_run lambda update-function-configuration --function-name "$LAMBDA_FUNCTION" --vpc-config "$VPC_CONFIG"
  if ! is_dry; then
    aws lambda wait function-updated --function-name "$LAMBDA_FUNCTION" --profile "$PROFILE" --region "$REGION"
  fi
else
  log "8. NOT moving the Lambda (SWITCH_LAMBDA_SUBNETS=0). When ready:"
  note "aws lambda update-function-configuration --profile $PROFILE --region $REGION --function-name $LAMBDA_FUNCTION --vpc-config '$VPC_CONFIG'"
fi

log "Done. Verify egress (README §1 'Verify'): trigger fx.daily and read job_run.error, or invoke the Lambda with {\"job\":\"fx.daily\",\"token\":…}."
