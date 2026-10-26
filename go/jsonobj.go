// JSON read a member at a time: deck requests, MCP messages and their
// tools' arguments, and task files are objects whose members are checked
// one by one, each read as the type its field needs.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
)

// jsonObj is a JSON object, each member still as its JSON text.
type jsonObj map[string]json.RawMessage

// parseObj: data as a JSON object; ok false when it is anything else.
func parseObj(data []byte) (jsonObj, bool) {
	var o jsonObj
	if json.Unmarshal(data, &o) != nil || o == nil {
		return nil, false
	}
	return o, true
}

func (o jsonObj) has(k string) bool { _, ok := o[k]; return ok }

// null: k is missing or null.
func (o jsonObj) null(k string) bool { return isNull(o[k]) }

// set sets k to v as JSON (to build a request in Go).
func (o jsonObj) set(k string, v any) {
	b, err := json.Marshal(v)
	if err != nil {
		panic(fmt.Sprintf("jsonObj.set %s: %v", k, err))
	}
	o[k] = b
}

func isNull(raw json.RawMessage) bool { return raw == nil || string(raw) == "null" }

// as reads raw as a T; ok false when it is missing, null or of another kind.
func as[T any](raw json.RawMessage) (T, bool) {
	var v T
	if isNull(raw) || json.Unmarshal(raw, &v) != nil {
		var zero T
		return zero, false
	}
	return v, true
}

// jsonTrue: raw is true.
func jsonTrue(raw json.RawMessage) bool {
	b, _ := as[bool](raw)
	return b
}

// wholeNumber: raw as a whole number (2 or 2.0), within ±1e18.
func wholeNumber(raw json.RawMessage) (int, bool) {
	f, ok := as[float64](raw)
	if !ok || !isWhole(f) || f > 1e18 || f < -1e18 {
		return 0, false
	}
	return int(f), true
}

// scalarText: a string as it is, a number or a bool as its JSON text
// (7, true); ok false for anything else.
func scalarText(raw json.RawMessage) (string, bool) {
	if s, ok := as[string](raw); ok {
		return s, true
	}
	if _, ok := as[float64](raw); ok {
		return string(raw), true
	}
	if b, ok := as[bool](raw); ok {
		return fmt.Sprint(b), true
	}
	return "", false
}

// marshalText is json.Marshal leaving <, > and & as they are: the encoder
// that writes it out escapes them for HTML when it should (API answers),
// a deck file keeps its text as written.
func marshalText(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(b.Bytes(), []byte("\n")), nil
}

// jsonText is v as one line of JSON, text as it is (no \u escapes for
// <, > and &, nor for non-ASCII characters).
func jsonText(v any) string {
	b, err := marshalText(v)
	if err != nil {
		panic(fmt.Sprintf("jsonText: %v", err))
	}
	return string(b)
}
