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
{{- /* A NetworkPolicy peer list where each entry is an ipBlock or names pods: a podSelector with matchLabels or
matchExpressions, and a namespaceSelector, when given, that names namespaces. Fails the render otherwise. */ -}}
{{- define "of-server.namedPeers" -}}
{{- $key := index . 0 -}}
{{- range index . 1 -}}
{{- if not (kindIs "map" .) -}}
{{- fail (printf "%s: each entry is a mapping (podSelector, namespaceSelector, ipBlock)" $key) -}}
{{- else if not (hasKey . "ipBlock") -}}
{{- $ps := index . "podSelector" -}}
{{- if not (and (kindIs "map" $ps) (or (index $ps "matchLabels") (index $ps "matchExpressions"))) -}}
{{- fail (printf "%s: each entry needs a podSelector with matchLabels or matchExpressions, or an ipBlock; a namespaceSelector alone, or with an empty or null podSelector, admits every pod of that namespace" $key) -}}
{{- end -}}
{{- if hasKey . "namespaceSelector" -}}
{{- $ns := index . "namespaceSelector" -}}
{{- if not (and (kindIs "map" $ns) (or (index $ns "matchLabels") (index $ns "matchExpressions"))) -}}
{{- fail (printf "%s: an empty or null namespaceSelector selects every namespace, so any pod with those labels anywhere gets in; name the namespace" $key) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- define "of-server.secretName" -}}{{ .Values.existingSecret | default (printf "%s-secrets" (include "of-server.name" .)) }}{{- end -}}
