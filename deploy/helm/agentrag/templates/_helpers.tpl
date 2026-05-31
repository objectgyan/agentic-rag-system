{{- define "agentrag.fullname" -}}
{{- printf "%s-agentrag" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentrag.labels" -}}
app.kubernetes.io/name: agentrag
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}
