// Leaf's views (src/views.py): the page shell every view shares, around
// each view's template (templates/, html/template, auto-escaped).
package main

import (
	"bytes"
	"embed"
	"html/template"
	"net/http"
	"strings"
)

//go:embed templates
var templateFS embed.FS

// views holds each view's template: the layout and partials, with the
// view's own blocks.
var views = map[string]*template.Template{}

func init() {
	base := template.Must(template.New("").ParseFS(templateFS, "templates/layout.html", "templates/partials/*.html"))
	for _, name := range []string{"login.html"} {
		views[name] = template.Must(template.Must(base.Clone()).ParseFS(templateFS, "templates/"+name))
	}
}

// currentModule is the module the request's view belongs to: "deck",
// "scratch", "wiki", or "" for views of none (search results, login).
func currentModule(r *http.Request) string {
	switch r.URL.Path {
	case "/deck":
		return "deck"
	case "/search", "/login":
		return ""
	}
	return "wiki"
}

// page renders a view (template name) in the page shell. actionButtons
// false drops the action buttons, new note and search (the login page has
// nothing to search or write); the editor has its own toolbar instead.
func page(w http.ResponseWriter, r *http.Request, status int, name, title string,
	editor, actionButtons bool, data map[string]any) {
	ctx := map[string]any{
		"title": title, "editor": editor, "wide": false,
		"show_actions": actionButtons && !editor,
		"module":       "", "deck_new": nil,
		"sharing":  sharingOn(r),
		"semantic": "off", "app_name": appName, "version": version, "build": cfg.Build,
		"allow_remote": cfg.AllowRemote, "locale": cfg.Locale, "date_format": cfg.DateFormat,
		// Only signed in: the login page has no one to sign out.
		"sign_out": cfg.Password != "" && isSignedIn(r),
	}
	if !editor {
		ctx["module"] = currentModule(r)
	}
	// The build's commit, shown in the corner: "abc1234 2026-09-26 12:00 +dirty".
	if fields := strings.Fields(cfg.Build); cfg.Build != "unknown" && len(fields) > 0 {
		ctx["commit"] = fields[0]
		ctx["dirty"] = strings.Contains(cfg.Build, "+dirty")
	}
	for k, v := range data {
		ctx[k] = v
	}
	var buf bytes.Buffer
	if err := views[name].ExecuteTemplate(&buf, "layout", ctx); err != nil {
		logf("rendering %s: %v", name, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(buf.Bytes())
}

const sharingCookie = "leaf-sharing"

// sharingOn: the request comes from a browser in sharing mode.
func sharingOn(r *http.Request) bool {
	c, err := r.Cookie(sharingCookie)
	return err == nil && c.Value == "1"
}

func isSignedIn(r *http.Request) bool {
	ok, _ := signedIn(r)
	return ok
}
