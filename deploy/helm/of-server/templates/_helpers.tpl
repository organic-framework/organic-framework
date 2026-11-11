{{- define "of-server.name" -}}{{ .Release.Name }}-of-server{{- end -}}
{{- define "of-server.labels" -}}
app.kubernetes.io/name: of-server
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "of-server.selector" -}}
app.kubernetes.io/name: of-server
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "of-server.secretName" -}}{{ .Values.existingSecret | default (printf "%s-secrets" (include "of-server.name" .)) }}{{- end -}}
