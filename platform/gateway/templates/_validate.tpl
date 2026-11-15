{{/*
Refuse to render a LoadBalancer that is open to the world. An empty
loadBalancerSourceRanges means "no restriction", so an unset value must fail, not
default to open.
*/}}
{{- define "gateway.sourceRanges" -}}
{{- $r := .Values.sourceRanges | default list -}}
{{- if eq (len $r) 0 -}}
{{- fail "sourceRanges is empty: refusing to render a public LoadBalancer with no source restriction (cluster-apps.sh sets it from terraform output ingress_source_ranges)" -}}
{{- end -}}
{{- range $r -}}
{{- if not (regexMatch "^([0-9]{1,3}\\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$" .) -}}
{{- fail (printf "sourceRanges entry %q is not an IPv4 CIDR" .) -}}
{{- end -}}
{{- if or (eq . "0.0.0.0/0") (hasSuffix "/0" .) -}}
{{- fail (printf "sourceRanges entry %q opens the gateway to the world" .) -}}
{{- end -}}
{{- end -}}
{{- toYaml $r -}}
{{- end -}}
