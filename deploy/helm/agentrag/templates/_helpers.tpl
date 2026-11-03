{{- define "agentrag.fullname" -}}
{{- printf "%s-agentrag" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentrag.labels" -}}
app.kubernetes.io/name: agentrag
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* The env Secret (a hook — see secret.yaml for why it is not named "-env"). */}}
{{- define "agentrag.envSecret" -}}
{{- printf "%s-config" (include "agentrag.fullname" .) -}}
{{- end -}}

{{- define "agentrag.modelCacheClaim" -}}
{{- .Values.modelCache.persistence.existingClaim | default (printf "%s-models" (include "agentrag.fullname" .)) -}}
{{- end -}}

{{/* Pod volume for HF_HOME (/models); paired with "agentrag.modelCacheMount". */}}
{{- define "agentrag.modelCacheVolume" -}}
- name: model-cache
{{- if .Values.modelCache.persistence.enabled }}
  persistentVolumeClaim:
    claimName: {{ include "agentrag.modelCacheClaim" . }}
{{- else }}
  emptyDir:
    sizeLimit: {{ .Values.modelCache.sizeLimit }}
{{- end }}
{{- end -}}

{{- define "agentrag.modelCacheMount" -}}
- name: model-cache
  mountPath: /models
{{- end -}}
