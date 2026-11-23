package main

import (
	"bufio"
	"net"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeSMTP: an SMTP server on localhost that takes mail without TLS
// (starttls: it says it offers STARTTLS, which a test of the refusal
// needs only to see). It keeps the commands and the mail's text.
type fakeSMTP struct {
	ln       net.Listener
	mu       sync.Mutex
	commands []string
	data     string
}

func startSMTP(t *testing.T, starttls bool) *fakeSMTP {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := &fakeSMTP{ln: ln}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go s.serve(conn, starttls)
		}
	}()
	return s
}

func (s *fakeSMTP) serve(conn net.Conn, starttls bool) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	say := func(line string) { conn.Write([]byte(line + "\r\n")) }
	say("220 fake ESMTP")
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return
		}
		line = strings.TrimRight(line, "\r\n")
		s.mu.Lock()
		s.commands = append(s.commands, line)
		s.mu.Unlock()
		switch verb := strings.ToUpper(strings.Fields(line + " x")[0]); verb {
		case "EHLO":
			if starttls {
				say("250-fake")
				say("250 STARTTLS")
			} else {
				say("250 fake")
			}
		case "DATA":
			say("354 go on")
			var b strings.Builder
			for {
				l, err := r.ReadString('\n')
				if err != nil || l == ".\r\n" {
					break
				}
				b.WriteString(l)
			}
			s.mu.Lock()
			s.data = b.String()
			s.mu.Unlock()
			say("250 ok")
		case "QUIT":
			say("221 bye")
			return
		default:
			say("250 ok")
		}
	}
}

func (s *fakeSMTP) config(tlsMode string) mailConfig {
	host, port, _ := net.SplitHostPort(s.ln.Addr().String())
	m, err := loadMail(envMap{"ZONE_SMTP_HOST": host, "ZONE_SMTP_PORT": port,
		"ZONE_SMTP_TLS": tlsMode, "ZONE_MAIL_FROM": "wiki@example.org"}.get)
	if err != nil {
		panic(err)
	}
	return m
}

type envMap map[string]string

func (e envMap) get(k string) string { return e[k] }

func TestSendMail(t *testing.T) {
	s := startSMTP(t, false)
	if err := sendMail(s.config("none"), "ann@example.org", "Grüße from Zonewiki", "Line one\nline two"); err != nil {
		t.Fatal(err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	cmds := strings.Join(s.commands, "\n")
	for _, want := range []string{"MAIL FROM:<wiki@example.org>", "RCPT TO:<ann@example.org>", "DATA", "QUIT"} {
		if !strings.Contains(cmds, want) {
			t.Errorf("no %q in %q", want, cmds)
		}
	}
	for _, want := range []string{"From: wiki@example.org\r\n", "To: ann@example.org\r\n",
		"Subject: =?utf-8?q?Gr=C3=BC=C3=9Fe_from_Zonewiki?=\r\n", "Content-Type: text/plain; charset=utf-8\r\n",
		"Message-ID: <", "@example.org>\r\n", "\r\n\r\nLine one\r\nline two\r\n"} {
		if !strings.Contains(s.data, want) {
			t.Errorf("no %q in %q", want, s.data)
		}
	}
}

func TestSendMailWantsSTARTTLS(t *testing.T) {
	s := startSMTP(t, false)
	err := sendMail(s.config("starttls"), "ann@example.org", "x", "y")
	if err == nil || !strings.Contains(err.Error(), "doesn't offer STARTTLS") {
		t.Fatalf("%v", err)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if strings.Contains(strings.Join(s.commands, "\n"), "RCPT") {
		t.Error("sent unencrypted")
	}
}

func TestSendMailServerDown(t *testing.T) {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := ln.Addr().String()
	ln.Close()
	host, port, _ := net.SplitHostPort(addr)
	m, _ := loadMail(envMap{"ZONE_SMTP_HOST": host, "ZONE_SMTP_PORT": port, "ZONE_SMTP_TLS": "none",
		"ZONE_MAIL_FROM": "w@example.org"}.get)
	defer func(d time.Duration) { mailDialTimeout = d }(mailDialTimeout)
	mailDialTimeout = 2 * time.Second
	if err := sendMail(m, "a@example.org", "x", "y"); err == nil || !strings.Contains(err.Error(), addr) {
		t.Fatalf("%v", err)
	}
}

func TestLoadMail(t *testing.T) {
	if m, err := loadMail(envMap{}.get); err != nil || m.Host != "" {
		t.Errorf("none: %v %+v", err, m)
	}
	m, err := loadMail(envMap{"ZONE_SMTP_HOST": "mail.example.org", "ZONE_MAIL_FROM": "w@example.org"}.get)
	if err != nil || m.Port != 587 || m.TLS != "starttls" {
		t.Errorf("defaults: %v %+v", err, m)
	}
	m, err = loadMail(envMap{"ZONE_SMTP_HOST": "m", "ZONE_SMTP_PORT": "465", "ZONE_MAIL_FROM": "w@example.org"}.get)
	if err != nil || m.TLS != "tls" {
		t.Errorf("465: %v %+v", err, m)
	}
	m, err = loadMail(envMap{"ZONE_SMTP_HOST": "m", "ZONE_SMTP_TLS": "TLS", "ZONE_MAIL_FROM": "w@example.org"}.get)
	if err != nil || m.Port != 465 {
		t.Errorf("tls: %v %+v", err, m)
	}
	for env, want := range map[string]envMap{
		"ZONE_SMTP_HOST isn't":          {"ZONE_MAIL_FROM": "w@example.org"},
		"ZONE_MAIL_FROM isn't":          {"ZONE_SMTP_HOST": "m"},
		"ZONE_MAIL_FROM=Wiki <w@x.org>": {"ZONE_SMTP_HOST": "m", "ZONE_MAIL_FROM": "Wiki <w@x.org>"},
		"ZONE_SMTP_PORT=lots":           {"ZONE_SMTP_HOST": "m", "ZONE_SMTP_PORT": "lots", "ZONE_MAIL_FROM": "w@x.org"},
		"ZONE_SMTP_TLS=maybe":           {"ZONE_SMTP_HOST": "m", "ZONE_SMTP_TLS": "maybe", "ZONE_MAIL_FROM": "w@x.org"},
		"ZONE_SMTP_USER isn't":          {"ZONE_SMTP_HOST": "m", "ZONE_SMTP_PASSWORD": "pw", "ZONE_MAIL_FROM": "w@x.org"},
	} {
		if _, err := loadMail(want.get); err == nil || !strings.Contains(err.Error(), env) {
			t.Errorf("%v: %v, want %q", want, err, env)
		}
	}
}

func TestValidEmail(t *testing.T) {
	for addr, want := range map[string]bool{
		"ann@example.org": true, "a.b+c@sub.example.co.uk": true,
		"ann": false, "ann@": false, "@example.org": false, "ann@localhost": false, "Ann <ann@example.org>": false,
		"ann@example.org\r\nBcc: x@y.org": false, "ann@exa mple.org": false, "a,b@example.org": false,
		strings.Repeat("a", 250) + "@x.org": false,
	} {
		if validEmail(addr) != want {
			t.Errorf("validEmail(%q) = %v", addr, !want)
		}
	}
}

func TestSignupSettings(t *testing.T) {
	hash := hashPassword("p")
	s, err := loadSettings(mustParse(t, "signup-domains = Example.org, @b.net\nsignup-role = Editor\n"+
		"signup-approval = yes\n[user a]\npassword = "+hash+"\nemail = A@Example.org\npending = yes\n"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(s.SignupDomains, " ") != "example.org b.net" || s.SignupRole != roleEditor || !s.SignupApproval {
		t.Errorf("%+v", s)
	}
	if u := s.Users["a"]; !u.Pending || !u.Disabled || u.Email != "a@example.org" {
		t.Errorf("%+v", u)
	}
	if userWithEmail(s, "a@example.org") == nil || userWithEmail(s, "b@example.org") != nil {
		t.Error("userWithEmail")
	}
	if !domainAllowed(s, "x@b.net") || domainAllowed(s, "x@c.net") || domainAllowed(s, "x@sub.example.org") {
		t.Error("domainAllowed")
	}
	if d, _ := loadSettings(mustParse(t, "")); d.SignupRole != roleViewer || d.SignupApproval || d.SignupDomains != nil {
		t.Errorf("defaults: %+v", d)
	}
	for text, want := range map[string]string{
		"signup-role = admin\n":                               "signup-role = admin isn't a role sign-up gives",
		"signup-approval = maybe\n":                           "signup-approval = maybe isn't yes or no",
		"signup-domains = example\n":                          "example isn't a mail domain",
		"[user a]\npassword = " + hash + "\npending = soon\n": "a's pending = soon isn't yes or no",
		"[user a]\npassword = " + hash + "\nemail = nope\n":   "a's email nope isn't a mail address",
	} {
		if _, err := loadSettings(mustParse(t, text)); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: %v", text, err)
		}
	}
	// Approving: pending and disabled both go.
	old, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\npending = yes\n"))
	now, _ := loadSettings(mustParse(t, "[user a]\npassword = "+hash+"\n"))
	if got := settingsChange(old, now); got != "Change settings: approve a" {
		t.Errorf("change: %q", got)
	}
}

func TestHourlyCap(t *testing.T) {
	c := newHourlyCap(2)
	if !c.take("a") || !c.take("a") || c.take("a") || !c.take("b") {
		t.Error("cap")
	}
}
