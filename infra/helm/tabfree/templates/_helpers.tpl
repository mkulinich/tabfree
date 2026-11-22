{{- define "tabfree.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "tabfree.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "tabfree.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "tabfree.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tabfree.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "tabfree.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ required "set image.digest (preferred) or image.tag" .Values.image.tag }}
{{- end -}}
{{- end -}}

{{/* The environment shared by every container: settings, then secrets from the existing Secret. */}}
{{- define "tabfree.env" -}}
{{- range $name, $value := .Values.config }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- range $name, $key := .Values.secrets.env }}
- name: {{ $name }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.secrets.existingSecret }}
      key: {{ $key }}
{{- end }}
{{- end -}}

{{/* Pod settings shared by every workload. */}}
{{- define "tabfree.podSpec" -}}
serviceAccountName: {{ include "tabfree.serviceAccountName" .root }}
securityContext:
  {{- toYaml .root.Values.podSecurityContext | nindent 2 }}
{{- with .root.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- if .root.Values.topologySpreadAcrossZones }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "tabfree.selector" . | nindent 8 }}
{{- end }}
volumes:
  - name: tmp
    emptyDir: {}
{{- end -}}

{{- define "tabfree.container" -}}
image: {{ include "tabfree.image" .root }}
imagePullPolicy: {{ .root.Values.image.pullPolicy }}
securityContext:
  {{- toYaml .root.Values.securityContext | nindent 2 }}
env:
  {{- include "tabfree.env" .root | nindent 2 }}
volumeMounts:
  - name: tmp
    mountPath: /tmp
{{- end -}}
