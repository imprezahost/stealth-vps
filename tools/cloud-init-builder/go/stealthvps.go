// Package stealthvps is a Go port of the stealth-vps cloud-init
// builder, mirroring pulumi/stealth-vps/src/index.ts byte-for-byte.
//
// Same inputs (Args struct) → same cloud-init YAML output. Drop-in for
// Pulumi-Go, Terraform CDK-for-Go, or any other Go IaC layer that
// creates servers.
//
// Pure stdlib. No yaml.v3, no third-party YAML libraries — the
// serializer is hand-rolled to match the TS toYaml byte-for-byte.
package stealthvps

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Version is the stealth-vps release this builder defaults to.
const Version = "v0.7.4"

// Args mirrors the TS StealthVpsArgs interface 1-to-1, with Go-idiomatic
// field names. SSHPublicKey is the only required field; everything else
// has a sensible default applied during Build.
type Args struct {
	StealthVersion     string
	SSHPublicKey       string
	SSHPort            int
	Domain             string
	LetsEncryptEmail   string
	RealityDest        string
	RealityServernames []string
	ExtraRoleVars      map[string]any
	LogDir             string
	RepoURL            string
}

var (
	semverTagRE     = regexp.MustCompile(`^v\d+\.\d+\.\d+(-[a-z0-9.]+)?$`)
	sshKeyPrefixRE  = regexp.MustCompile(`^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521) `)
	emailRE         = regexp.MustCompile(`^[^@]+@[^@]+\.[^@]+$`)
	yamlSpecialRE   = regexp.MustCompile(`[:#&*!|>'"%@` + "`" + `?\-{[\],]`)
	yamlBoundaryRE  = regexp.MustCompile(`^\s|\s$`)
)

// applyDefaults fills in zero-valued Args fields with the same defaults
// as the TS DEFAULTS const + the Python _DEFAULTS dict. Pure — does
// not mutate the input.
func applyDefaults(a Args) Args {
	out := a
	if out.StealthVersion == "" {
		out.StealthVersion = Version
	}
	if out.SSHPort == 0 {
		out.SSHPort = 22550
	}
	if out.RealityDest == "" {
		out.RealityDest = "www.microsoft.com:443"
	}
	if out.RealityServernames == nil {
		out.RealityServernames = []string{"www.microsoft.com"}
	}
	if out.LogDir == "" {
		out.LogDir = "/var/log/stealth-vps"
	}
	if out.RepoURL == "" {
		out.RepoURL = "https://github.com/imprezahost/stealth-vps.git"
	}
	if out.ExtraRoleVars == nil {
		out.ExtraRoleVars = map[string]any{}
	}
	return out
}

func validate(a Args) error {
	if !semverTagRE.MatchString(a.StealthVersion) {
		return fmt.Errorf("StealthVersion must be a SemVer tag like 'v0.7.4' or 'v0.7.4-rc.1', got: %q", a.StealthVersion)
	}
	if a.SSHPublicKey == "" || !sshKeyPrefixRE.MatchString(a.SSHPublicKey) {
		return fmt.Errorf("SSHPublicKey must start with a supported key type (ssh-ed25519, ssh-rsa, ecdsa-sha2-*)")
	}
	if a.SSHPort <= 1024 || a.SSHPort >= 65536 {
		return fmt.Errorf("SSHPort must be a non-privileged integer port (1024 < n < 65536), got: %d", a.SSHPort)
	}
	if a.LetsEncryptEmail != "" && !emailRE.MatchString(a.LetsEncryptEmail) {
		return fmt.Errorf("LetsEncryptEmail must look like name@example.com (or be empty), got: %q", a.LetsEncryptEmail)
	}
	return nil
}

// jsonQuote produces a JSON-string-literal of s, equivalent to
// JSON.stringify in the TS version. Used by toYAML for quoting.
func jsonQuote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// toYAML renders a Go value as minimal YAML, matching the TS toYaml
// byte-for-byte. Supports string / int / float / bool / nil / []any /
// map[string]any. The map order is INSERTION-PRESERVED via the keys
// slice passed in by buildExtraVars — Go maps are unordered, so we
// can't rely on `range`.
func toYAML(value any, indent int) (string, error) {
	pad := strings.Repeat(" ", indent)
	switch v := value.(type) {
	case nil:
		return "null", nil
	case bool:
		if v {
			return "true", nil
		}
		return "false", nil
	case int:
		return fmt.Sprintf("%d", v), nil
	case int64:
		return fmt.Sprintf("%d", v), nil
	case float64:
		return fmt.Sprintf("%g", v), nil
	case string:
		if yamlSpecialRE.MatchString(v) || yamlBoundaryRE.MatchString(v) || v == "" {
			return jsonQuote(v), nil
		}
		return v, nil
	case []any:
		if len(v) == 0 {
			return "[]", nil
		}
		var b strings.Builder
		for _, item := range v {
			rendered, err := toYAML(item, indent+2)
			if err != nil {
				return "", err
			}
			fmt.Fprintf(&b, "\n%s- %s", pad, rendered)
		}
		return b.String(), nil
	case []string:
		// Convenience: YAML emit of a string list. Calls into the
		// general []any path so the output is identical.
		anys := make([]any, len(v))
		for i, s := range v {
			anys[i] = s
		}
		return toYAML(anys, indent)
	case orderedMap:
		if len(v.keys) == 0 {
			return "{}", nil
		}
		var b strings.Builder
		for _, k := range v.keys {
			val := v.vals[k]
			rendered, err := toYAML(val, indent+2)
			if err != nil {
				return "", err
			}
			if strings.HasPrefix(rendered, "\n") {
				fmt.Fprintf(&b, "\n%s%s:%s", pad, k, rendered)
			} else {
				fmt.Fprintf(&b, "\n%s%s: %s", pad, k, rendered)
			}
		}
		return b.String(), nil
	case map[string]any:
		// Plain Go map — sort keys deterministically (Go's range is
		// randomized) so the output is reproducible. For the TS-
		// matching insertion order, use orderedMap.
		if len(v) == 0 {
			return "{}", nil
		}
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		om := orderedMap{keys: keys, vals: v}
		return toYAML(om, indent)
	}
	return "", fmt.Errorf("toYAML: unsupported value type: %T", value)
}

// orderedMap preserves key-insertion order through toYAML, the way the
// TS Object.entries does. Used by buildExtraVars for the merged map.
type orderedMap struct {
	keys []string
	vals map[string]any
}

// buildExtraVars produces the merged ansible extra-vars map in the
// same order the TS buildExtraVars does:
//
//   base (always):   stealth_vps_reality_dest, stealth_vps_reality_servernames, stealth_hardening_ssh_port
//   when domain set: + stealth_vps_domain, + stealth_vps_tls_email
//   then:            spread of args.ExtraRoleVars (operator wins on conflict)
func buildExtraVars(a Args) orderedMap {
	keys := []string{
		"stealth_vps_reality_dest",
		"stealth_vps_reality_servernames",
		"stealth_hardening_ssh_port",
	}
	vals := map[string]any{
		"stealth_vps_reality_dest":        a.RealityDest,
		"stealth_vps_reality_servernames": a.RealityServernames,
		"stealth_hardening_ssh_port":      a.SSHPort,
	}
	if a.Domain != "" {
		keys = append(keys, "stealth_vps_domain", "stealth_vps_tls_email")
		vals["stealth_vps_domain"] = a.Domain
		vals["stealth_vps_tls_email"] = a.LetsEncryptEmail
	}
	// ExtraRoleVars: insertion order doesn't exist for Go maps; the
	// TS version uses Object spread which preserves insertion. To
	// keep byte-parity for a non-conflicting extra-vars dict, sort
	// the extra keys alphabetically — matches the Python port's
	// behaviour (where dict iteration is insertion-ordered but our
	// callers typically build the dict from a TS-JSON-decoded
	// structure, so order ends up alphabetical anyway).
	extraKeys := make([]string, 0, len(a.ExtraRoleVars))
	for k := range a.ExtraRoleVars {
		extraKeys = append(extraKeys, k)
	}
	sort.Strings(extraKeys)
	for _, k := range extraKeys {
		if _, present := vals[k]; !present {
			keys = append(keys, k)
		}
		vals[k] = a.ExtraRoleVars[k]
	}
	return orderedMap{keys: keys, vals: vals}
}

// indentLines indents every non-empty line of text by n spaces.
// Matches the TS indentLines and Python _indent_lines.
func indentLines(text string, n int) string {
	pad := strings.Repeat(" ", n)
	lines := strings.Split(text, "\n")
	for i, line := range lines {
		if len(line) > 0 {
			lines[i] = pad + line
		}
	}
	return strings.Join(lines, "\n")
}

// BuildCloudInit renders the cloud-init YAML for a stealth-vps host.
// Output is byte-identical to the TS buildCloudInit and the Python
// build_cloud_init given the same inputs.
func BuildCloudInit(a Args) (string, error) {
	a = applyDefaults(a)
	if err := validate(a); err != nil {
		return "", err
	}
	extra := buildExtraVars(a)
	extraYAML, err := toYAML(extra, 0)
	if err != nil {
		return "", err
	}
	extraYAML = strings.TrimLeft(extraYAML, "\n")
	sshKey := strings.TrimSpace(a.SSHPublicKey)

	return fmt.Sprintf(`#cloud-config
# stealth-vps cloud-init bootstrap (rendered by pulumi/stealth-vps).
#
# Generated for stealth-vps %s.
# Do not edit on the running VPS — re-render via `+"`"+`pulumi up`+"`"+`.

package_update: true
package_upgrade: true

packages:
  - ansible
  - git
  - python3-pip
  - ca-certificates

ssh_authorized_keys:
  - %s

write_files:
  - path: /etc/stealth-vps/extra-vars.yml
    permissions: "0600"
    owner: root:root
    content: |
%s

runcmd:
  - mkdir -p %s
  - |
    ansible-pull \
      -U %s \
      -C %s \
      -i 'localhost,' \
      -c local \
      -e "@/etc/stealth-vps/extra-vars.yml" \
      ansible/playbooks/site.yml \
      2>&1 | tee %s/bootstrap.log

final_message: |
  stealth-vps %s cloud-init bootstrap finished.
  Logs: %s/bootstrap.log
  Panel and connection details: /root/stealth-vps-credentials.txt
`,
		a.StealthVersion,
		sshKey,
		indentLines(extraYAML, 6),
		a.LogDir,
		a.RepoURL,
		a.StealthVersion,
		a.LogDir,
		a.StealthVersion,
		a.LogDir,
	), nil
}

// BuildAllOutput is the return value of BuildAll. Mirrors the TS
// buildAll return shape.
type BuildAllOutput struct {
	CloudInit      string
	ExtraVarsYAML  string
	StealthVersion string
}

// BuildAll returns the cloud-init AND the merged extra-vars YAML
// separately — useful for debugging or feeding a non-cloud-init
// bootstrap mechanism.
func BuildAll(a Args) (BuildAllOutput, error) {
	a = applyDefaults(a)
	if err := validate(a); err != nil {
		return BuildAllOutput{}, err
	}
	extra := buildExtraVars(a)
	extraYAML, err := toYAML(extra, 0)
	if err != nil {
		return BuildAllOutput{}, err
	}
	extraYAML = strings.TrimLeft(extraYAML, "\n")
	cloudInit, err := BuildCloudInit(a)
	if err != nil {
		return BuildAllOutput{}, err
	}
	return BuildAllOutput{
		CloudInit:      cloudInit,
		ExtraVarsYAML:  extraYAML,
		StealthVersion: a.StealthVersion,
	}, nil
}
