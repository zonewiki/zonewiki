// Mail (docs/spec/deployment.md, Mail): Zonewiki sends a mail for sign-up
// (signup.go) and about a task given to a user (notify.go), through the
// SMTP server the deployment names in ZONE_SMTP_HOST … (.env). Go's net/smtp, no dependency. The connection is
// encrypted unless ZONE_SMTP_TLS=none says otherwise: STARTTLS, which the
// server must offer (587), or TLS from the start (465).
package main

import (
	"crypto/rand"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"mime"
	"net"
	"net/mail"
	"net/smtp"
	"strconv"
	"strings"
	"time"
)

// mailConfig: the SMTP server Zonewiki sends through.
type mailConfig struct {
	Host           string // "": Zonewiki sends no mail
	Port           int
	User, Password string // Password only with User: AUTH PLAIN
	From           string // the sender's address (ZONE_MAIL_FROM)
	TLS            string // "starttls", "tls" or "none"
}

// loadMail reads ZONE_SMTP_HOST, _PORT, _USER, _PASSWORD, _TLS and
// ZONE_MAIL_FROM, or says what is wrong with them.
func loadMail(getenv func(string) string) (mailConfig, error) {
	m := mailConfig{Host: strings.TrimSpace(getenv("ZONE_SMTP_HOST")), User: getenv("ZONE_SMTP_USER"),
		Password: getenv("ZONE_SMTP_PASSWORD"), From: strings.TrimSpace(getenv("ZONE_MAIL_FROM")),
		TLS: strings.ToLower(strings.TrimSpace(getenv("ZONE_SMTP_TLS")))}
	port := strings.TrimSpace(getenv("ZONE_SMTP_PORT"))
	if m.Host == "" {
		for _, key := range []string{"ZONE_SMTP_PORT", "ZONE_SMTP_USER", "ZONE_SMTP_PASSWORD", "ZONE_SMTP_TLS", "ZONE_MAIL_FROM"} {
			if getenv(key) != "" {
				return m, fmt.Errorf("%s is set, but ZONE_SMTP_HOST isn't: set the mail server's name too", key)
			}
		}
		return m, nil
	}
	if port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return m, fmt.Errorf("ZONE_SMTP_PORT=%s isn't a port (e.g. 587)", port)
		}
		m.Port = n
	}
	switch {
	case m.TLS == "" && m.Port == 465:
		m.TLS = "tls"
	case m.TLS == "":
		m.TLS = "starttls"
	case m.TLS != "starttls" && m.TLS != "tls" && m.TLS != "none":
		return m, fmt.Errorf("ZONE_SMTP_TLS=%s isn't known: use starttls (the default), tls or none", m.TLS)
	}
	if m.Port == 0 {
		m.Port = 587
		if m.TLS == "tls" {
			m.Port = 465
		}
	}
	if m.Password != "" && m.User == "" {
		return m, errors.New("ZONE_SMTP_PASSWORD is set, but ZONE_SMTP_USER isn't: set the user it belongs to")
	}
	if m.From == "" {
		return m, errors.New("ZONE_SMTP_HOST is set, but ZONE_MAIL_FROM isn't: set the address Zonewiki's mail comes from " +
			"(e.g. ZONE_MAIL_FROM=wiki@example.org)")
	}
	if a, err := mail.ParseAddress(m.From); err != nil || a.Name != "" {
		return m, fmt.Errorf("ZONE_MAIL_FROM=%s isn't an address (e.g. wiki@example.org)", m.From)
	}
	return m, nil
}

// How long a mail may take: to connect, and in all.
var (
	mailDialTimeout = 20 * time.Second
	mailTimeout     = time.Minute
)

// sendMail sends a plain-text mail to the address to.
func sendMail(m mailConfig, to, subject, body string) error {
	if m.Host == "" {
		return errors.New("Zonewiki has no mail server (ZONE_SMTP_HOST)")
	}
	addr := net.JoinHostPort(m.Host, strconv.Itoa(m.Port))
	dialer := &net.Dialer{Timeout: mailDialTimeout}
	tlsConfig := &tls.Config{ServerName: m.Host, MinVersion: tls.VersionTLS12}
	var conn net.Conn
	var err error
	if m.TLS == "tls" {
		conn, err = tls.DialWithDialer(dialer, "tcp", addr, tlsConfig)
	} else {
		conn, err = dialer.Dial("tcp", addr)
	}
	if err != nil {
		return fmt.Errorf("mail server %s: %w", addr, err)
	}
	_ = conn.SetDeadline(time.Now().Add(mailTimeout))
	c, err := smtp.NewClient(conn, m.Host)
	if err != nil {
		conn.Close()
		return fmt.Errorf("mail server %s: %w", addr, err)
	}
	defer c.Close()
	if m.TLS == "starttls" {
		if ok, _ := c.Extension("STARTTLS"); !ok {
			return fmt.Errorf("mail server %s doesn't offer STARTTLS: use ZONE_SMTP_TLS=tls for port 465, "+
				"or none for a server that takes mail unencrypted", addr)
		}
		if err := c.StartTLS(tlsConfig); err != nil {
			return fmt.Errorf("mail server %s: STARTTLS: %w", addr, err)
		}
	}
	if m.User != "" {
		// PlainAuth refuses to send the password unencrypted, except to
		// this machine.
		if err := c.Auth(smtp.PlainAuth("", m.User, m.Password, m.Host)); err != nil {
			return fmt.Errorf("mail server %s: signing in as %s: %w", addr, m.User, err)
		}
	}
	if err := c.Mail(m.From); err != nil {
		return fmt.Errorf("mail server %s: from %s: %w", addr, m.From, err)
	}
	if err := c.Rcpt(to); err != nil {
		return fmt.Errorf("mail server %s: to %s: %w", addr, to, err)
	}
	w, err := c.Data()
	if err != nil {
		return fmt.Errorf("mail server %s: %w", addr, err)
	}
	if _, err := w.Write([]byte(mailMessage(m.From, to, subject, body))); err != nil {
		return fmt.Errorf("mail server %s: %w", addr, err)
	}
	if err := w.Close(); err != nil {
		return fmt.Errorf("mail server %s: %w", addr, err)
	}
	return c.Quit()
}

// mailMessage: the mail's headers and text (net/smtp turns its line ends
// into CRLF). to and from are checked addresses; the subject is Zonewiki's,
// encoded when it isn't ASCII.
func mailMessage(from, to, subject, body string) string {
	id := make([]byte, 12)
	_, _ = rand.Read(id)
	_, domain, _ := strings.Cut(from, "@")
	var b strings.Builder
	for _, h := range [][2]string{
		{"From", from}, {"To", to}, {"Subject", mime.QEncoding.Encode("utf-8", subject)},
		{"Date", now().Format(time.RFC1123Z)},
		{"Message-ID", "<" + hex.EncodeToString(id) + "@" + domain + ">"},
		{"MIME-Version", "1.0"}, {"Content-Type", "text/plain; charset=utf-8"},
		{"Content-Transfer-Encoding", "8bit"}, {"Auto-Submitted", "auto-generated"},
	} {
		b.WriteString(h[0] + ": " + h[1] + "\n")
	}
	b.WriteString("\n" + strings.ReplaceAll(body, "\r", ""))
	if !strings.HasSuffix(body, "\n") {
		b.WriteString("\n")
	}
	return b.String()
}
