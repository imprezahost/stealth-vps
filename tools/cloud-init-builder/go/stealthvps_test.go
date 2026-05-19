package stealthvps

import (
	"strings"
	"testing"
)

func TestMinimumRequiredArgs(t *testing.T) {
	out, err := BuildCloudInit(Args{
		SSHPublicKey: "ssh-ed25519 AAAA test@example.com",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.HasPrefix(out, "#cloud-config") {
		t.Errorf("output should start with #cloud-config, got: %q", out[:30])
	}
	if !strings.Contains(out, "stealth-vps v0.7.4 cloud-init bootstrap finished") {
		t.Errorf("output should mention the version, got: %s", out)
	}
}

func TestInvalidStealthVersion(t *testing.T) {
	_, err := BuildCloudInit(Args{
		SSHPublicKey:   "ssh-ed25519 AAAA test@example.com",
		StealthVersion: "0.7.4", // missing leading v
	})
	if err == nil || !strings.Contains(err.Error(), "SemVer tag") {
		t.Errorf("expected SemVer validation error, got: %v", err)
	}
}

func TestInvalidSSHKey(t *testing.T) {
	_, err := BuildCloudInit(Args{
		SSHPublicKey: "not-a-real-key",
	})
	if err == nil || !strings.Contains(err.Error(), "supported key type") {
		t.Errorf("expected SSH key validation error, got: %v", err)
	}
}

func TestPrivilegedSSHPortRejected(t *testing.T) {
	_, err := BuildCloudInit(Args{
		SSHPublicKey: "ssh-ed25519 AAAA test@example.com",
		SSHPort:      22,
	})
	if err == nil || !strings.Contains(err.Error(), "non-privileged") {
		t.Errorf("expected port validation error, got: %v", err)
	}
}

func TestInvalidLEEmail(t *testing.T) {
	_, err := BuildCloudInit(Args{
		SSHPublicKey:     "ssh-ed25519 AAAA test@example.com",
		LetsEncryptEmail: "not-an-email",
	})
	if err == nil || !strings.Contains(err.Error(), "name@example.com") {
		t.Errorf("expected email validation error, got: %v", err)
	}
}

func TestEmptyLEEmailAccepted(t *testing.T) {
	out, err := BuildCloudInit(Args{
		SSHPublicKey:     "ssh-ed25519 AAAA test@example.com",
		LetsEncryptEmail: "",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.Contains(out, "tls_email") {
		t.Error("no domain set → tls_email shouldn't appear in output")
	}
}

func TestDomainAddsTLSEmail(t *testing.T) {
	out, err := BuildCloudInit(Args{
		SSHPublicKey:     "ssh-ed25519 AAAA test@example.com",
		Domain:           "vpn.example.com",
		LetsEncryptEmail: "ops@example.com",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, "stealth_vps_domain: vpn.example.com") {
		t.Errorf("output should contain unquoted domain, got: %s", out)
	}
	// ops@example.com has `@` → JSON-quoted per the toYAML regex.
	if !strings.Contains(out, `stealth_vps_tls_email: "ops@example.com"`) {
		t.Errorf("output should contain quoted email, got: %s", out)
	}
}

func TestExtraRoleVarsOverrideBase(t *testing.T) {
	out, err := BuildCloudInit(Args{
		SSHPublicKey: "ssh-ed25519 AAAA test@example.com",
		RealityDest:  "www.cloudflare.com:443",
		ExtraRoleVars: map[string]any{
			"stealth_vps_reality_dest": "www.bing.com:443",
		},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(out, `stealth_vps_reality_dest: "www.bing.com:443"`) {
		t.Errorf("override should win, got: %s", out)
	}
	if strings.Contains(out, "www.cloudflare.com:443") {
		t.Errorf("base value should not appear after override, got: %s", out)
	}
}

func TestBuildAllSplitOutputs(t *testing.T) {
	result, err := BuildAll(Args{
		SSHPublicKey: "ssh-ed25519 AAAA test@example.com",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.StealthVersion != "v0.7.4" {
		t.Errorf("expected v0.7.4, got %q", result.StealthVersion)
	}
	if strings.HasPrefix(result.ExtraVarsYAML, "#cloud-config") {
		t.Error("ExtraVarsYAML should not include the cloud-config header")
	}
	if !strings.Contains(result.ExtraVarsYAML, "stealth_vps_reality_dest") {
		t.Errorf("ExtraVarsYAML should contain reality dest, got: %s", result.ExtraVarsYAML)
	}
}

func TestToYAMLBasicTypes(t *testing.T) {
	cases := []struct {
		in   any
		want string
	}{
		{nil, "null"},
		{true, "true"},
		{false, "false"},
		{42, "42"},
		{"plain", "plain"},
		{"has:colon", `"has:colon"`},
		{"", `""`},
		{[]any{}, "[]"},
		{map[string]any{}, "{}"},
	}
	for _, c := range cases {
		got, err := toYAML(c.in, 0)
		if err != nil {
			t.Errorf("toYAML(%v) errored: %v", c.in, err)
			continue
		}
		if got != c.want {
			t.Errorf("toYAML(%v) = %q, want %q", c.in, got, c.want)
		}
	}
}
