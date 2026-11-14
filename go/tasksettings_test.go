package main

import (
	"errors"
	"reflect"
	"testing"
)

func TestSetEmbedArgs(t *testing.T) {
	cases := []struct {
		args string
		set  map[string]string
		want string
	}{
		{"", map[string]string{"view": "open"}, "view=open"},
		{"tag=work view=focus", map[string]string{"view": "plan"}, "tag=work view=plan"},
		{"tag=work view=focus limit=5", map[string]string{"view": ""}, "tag=work limit=5"},
		// New ones go last, in the keys' order; what it doesn't know stays.
		{"keep=1 odd", map[string]string{"limit": "3", "tag": "a|b"}, "keep=1 odd tag=a|b limit=3"},
		// A param written twice is written once, where it first was.
		{"View=open list=shop view=plan", map[string]string{"view": "focus"}, "view=focus list=shop"},
	}
	for _, c := range cases {
		if got := setEmbedArgs(c.args, c.set); got != c.want {
			t.Errorf("setEmbedArgs(%q, %v) = %q, want %q", c.args, c.set, got, c.want)
		}
	}
}

func TestSetEmbedParams(t *testing.T) {
	text := "# Shop\n\n`[[tasks: tag=code]]`\n\n[[tasks:]]\n\ntext [[Tasks: list=shop  keep=1 ]] more\n\n[[TASKS:]]\n\n[[tasks: tag=a]]\n"
	cases := []struct {
		n    int
		was  string
		set  map[string]string
		want string
	}{
		{0, "", map[string]string{"view": "open"}, "[[tasks: view=open]]"},
		{1, "list=shop  keep=1 ", map[string]string{"limit": "4"}, "[[Tasks: list=shop keep=1 limit=4]]"},
		{1, "list=shop  keep=1 ", map[string]string{"keep": ""}, "[[Tasks: list=shop]]"},
		{2, "", map[string]string{"plan": "off"}, "[[TASKS: plan=off]]"},
		{3, "tag=a", map[string]string{"tag": ""}, "[[tasks:]]"},
	}
	for _, c := range cases {
		got, err := setEmbedParams(text, c.n, c.was, c.set)
		if err != nil {
			t.Errorf("setEmbedParams(%d, %q): %v", c.n, c.was, err)
			continue
		}
		embeds := tasksRE.FindAllString(got, -1)
		// The one in code is found here (the text isn't masked) and stays.
		if embeds[0] != "[[tasks: tag=code]]" || embeds[c.n+1] != c.want {
			t.Errorf("setEmbedParams(%d, %v): embeds %q, want %q", c.n, c.set, embeds, c.want)
		}
		if len(got)-len(c.want) != len(text)-len(tasksRE.FindAllString(text, -1)[c.n+1]) {
			t.Errorf("setEmbedParams(%d, %v) changed more than the embed: %q", c.n, c.set, got)
		}
	}
	// The embed isn't as the dialog saw it, or isn't there.
	for _, c := range []struct {
		n   int
		was string
	}{{0, "view=open"}, {1, "list=shop keep=1"}, {4, ""}, {-1, ""}} {
		if _, err := setEmbedParams(text, c.n, c.was, map[string]string{"view": "open"}); !errors.Is(err, errEmbedChanged) {
			t.Errorf("setEmbedParams(%d, %q): %v, want errEmbedChanged", c.n, c.was, err)
		}
	}
}

func TestCheckEmbedSettings(t *testing.T) {
	got, err := checkEmbedSettings(map[string]string{"view": " Open ", "plan": "OFF", "limit": "7",
		"new": "#a,b", "tag": "a|b", "labels": "To_buy,Cart"}, embedSettingKeys)
	want := map[string]string{"view": "open", "plan": "off", "limit": "7", "new": "a,b",
		"tag": "a|b", "labels": "To_buy,Cart"}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Errorf("checkEmbedSettings = %v, %v, want %v", got, err, want)
	}
	for _, bad := range []map[string]string{
		{"view": "week"}, {"plan": "maybe"}, {"limit": "0"}, {"limit": "100"}, {"limit": "x"}, {"pin": "1"},
		{"tag": "a b"}, {"tag": "a]]"}, {"labels": "a`b"}, {"keep": "1"}, {"list": "shop"},
	} {
		if _, err := checkEmbedSettings(bad, embedSettingKeys); err == nil {
			t.Errorf("checkEmbedSettings(%v) passed", bad)
		}
	}
	// All tasks has no filter, list or pin.
	for _, k := range []string{"tag", "list", "pin"} {
		if _, err := checkEmbedSettings(map[string]string{k: "1"}, allSettingKeys); err == nil {
			t.Errorf("checkEmbedSettings(%s) passed for All tasks", k)
		}
	}
}
