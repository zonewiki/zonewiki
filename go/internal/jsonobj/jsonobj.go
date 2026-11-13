// Package jsonobj reads JSON a member at a time: task requests, MCP
// messages and their tools' arguments, and task files are objects whose
// members are checked one by one, each read as the type its field needs.
package jsonobj

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
)

// Obj is a JSON object, each member still as its JSON text.
type Obj map[string]json.RawMessage

// Parse: data as a JSON object; ok false when it is anything else.
func Parse(data []byte) (Obj, bool) {
	var o Obj
	if json.Unmarshal(data, &o) != nil || o == nil {
		return nil, false
	}
	return o, true
}

func (o Obj) Has(k string) bool { _, ok := o[k]; return ok }

// Null: k is missing or null.
func (o Obj) Null(k string) bool { return IsNull(o[k]) }

// Set sets k to v as JSON (to build a request in Go).
func (o Obj) Set(k string, v any) {
	b, err := json.Marshal(v)
	if err != nil {
		panic(fmt.Sprintf("jsonobj: Set %s: %v", k, err))
	}
	o[k] = b
}

func IsNull(raw json.RawMessage) bool { return raw == nil || string(raw) == "null" }

// As reads raw as a T; ok false when it is missing, null or of another kind.
func As[T any](raw json.RawMessage) (T, bool) {
	var v T
	if IsNull(raw) || json.Unmarshal(raw, &v) != nil {
		var zero T
		return zero, false
	}
	return v, true
}

// True: raw is true.
func True(raw json.RawMessage) bool {
	b, _ := As[bool](raw)
	return b
}

// WholeNumber: raw as a whole number (2 or 2.0), within ±1e18.
func WholeNumber(raw json.RawMessage) (int, bool) {
	f, ok := As[float64](raw)
	if !ok || f != math.Trunc(f) || math.IsInf(f, 0) || f > 1e18 || f < -1e18 {
		return 0, false
	}
	return int(f), true
}

// ScalarText: a string as it is, a number or a bool as its JSON text
// (7, true); ok false for anything else.
func ScalarText(raw json.RawMessage) (string, bool) {
	if s, ok := As[string](raw); ok {
		return s, true
	}
	if _, ok := As[float64](raw); ok {
		return string(raw), true
	}
	if b, ok := As[bool](raw); ok {
		return fmt.Sprint(b), true
	}
	return "", false
}

// Str: member k as text (ScalarText), "" when it is anything else (a file
// edited by hand).
func (o Obj) Str(k string) string {
	s, _ := ScalarText(o[k])
	return s
}

// TextList: a list's items as text (ScalarText), the others left out.
func TextList(raw json.RawMessage) []string {
	l, _ := As[[]json.RawMessage](raw)
	var out []string
	for _, x := range l {
		if s, ok := ScalarText(x); ok {
			out = append(out, s)
		}
	}
	return out
}

// List: a list's objects, the other items left out.
func List(raw json.RawMessage) []Obj {
	l, _ := As[[]json.RawMessage](raw)
	var out []Obj
	for _, x := range l {
		if o, ok := As[Obj](x); ok {
			out = append(out, o)
		}
	}
	return out
}

// Nullable: "" as null.
func Nullable(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// MarshalText is json.Marshal leaving <, > and & as they are: the encoder
// that writes it out escapes them for HTML when it should (API answers),
// a task list file keeps its text as written.
func MarshalText(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(b.Bytes(), []byte("\n")), nil
}

// Text is v as one line of JSON, text as it is (no \u escapes for
// <, > and &, nor for non-ASCII characters).
func Text(v any) string {
	b, err := MarshalText(v)
	if err != nil {
		panic(fmt.Sprintf("jsonobj: Text: %v", err))
	}
	return string(b)
}
