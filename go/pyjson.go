// JSON as Python's json module reads and writes it: objects keep their keys'
// order (a dict's), numbers stay ints or floats. Requests and MCP messages
// are read so (their checks are the Python Leaf's).
//
// Values: nil, bool, string, int64, float64, []any, *Obj.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
)

// Obj is a JSON object that keeps its keys in order, as a Python dict.
type Obj struct {
	keys []string
	m    map[string]any
}

func NewObj(kv ...any) *Obj {
	o := &Obj{m: map[string]any{}}
	for i := 0; i+1 < len(kv); i += 2 {
		o.Set(kv[i].(string), kv[i+1])
	}
	return o
}

func (o *Obj) Get(k string) any            { return o.m[k] }
func (o *Obj) Has(k string) bool           { _, ok := o.m[k]; return ok }
func (o *Obj) Keys() []string              { return o.keys }
func (o *Obj) Len() int                    { return len(o.keys) }
func (o *Obj) Lookup(k string) (any, bool) { v, ok := o.m[k]; return v, ok }

// Set sets k; a new key goes last, an existing one keeps its place.
func (o *Obj) Set(k string, v any) {
	if _, ok := o.m[k]; !ok {
		o.keys = append(o.keys, k)
	}
	o.m[k] = v
}

func (o *Obj) Delete(k string) {
	if _, ok := o.m[k]; !ok {
		return
	}
	delete(o.m, k)
	for i, key := range o.keys {
		if key == k {
			o.keys = append(o.keys[:i:i], o.keys[i+1:]...)
			break
		}
	}
}

// Str is k's value when it is a string, else "".
func (o *Obj) Str(k string) string {
	s, _ := o.m[k].(string)
	return s
}

// Truthy is Python's bool(o.get(k)).
func (o *Obj) Truthy(k string) bool { return truthyValue(o.m[k]) }

func truthyValue(v any) bool {
	switch v := v.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case int64:
		return v != 0
	case float64:
		return v != 0
	case []any:
		return len(v) > 0
	case *Obj:
		return v.Len() > 0
	}
	return true
}

// List is k's value when it is a list, else nil.
func (o *Obj) List(k string) []any {
	l, _ := o.m[k].([]any)
	return l
}

// Copy is a shallow copy (Python's dict(o)).
func (o *Obj) Copy() *Obj {
	c := &Obj{keys: append([]string(nil), o.keys...), m: make(map[string]any, len(o.m))}
	for k, v := range o.m {
		c.m[k] = v
	}
	return c
}

// MarshalJSON writes the keys in order. It leaves <, > and & as they are:
// the encoder that called it escapes them for HTML when it should (API
// answers), a deck file keeps its text as written.
func (o *Obj) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		if err := enc.Encode(k); err != nil {
			return nil, err
		}
		b.Truncate(b.Len() - 1) // Encode's newline
		b.WriteByte(':')
		if err := enc.Encode(jsonable(o.m[k])); err != nil {
			return nil, err
		}
		b.Truncate(b.Len() - 1)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

// jsonable: a float Go's encoder can't write (NaN, ±Inf) as null.
func jsonable(v any) any {
	if f, ok := v.(float64); ok && (math.IsNaN(f) || math.IsInf(f, 0)) {
		return nil
	}
	return v
}

func (o *Obj) UnmarshalJSON(data []byte) error {
	v, err := loads(data)
	if err != nil {
		return err
	}
	obj, ok := v.(*Obj)
	if !ok {
		return errors.New("not a JSON object")
	}
	*o = *obj
	return nil
}

// loads parses JSON as Python's json.loads: objects as *Obj, numbers as
// int64 (no fraction or exponent) or float64.
func loads(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := decodeValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("extra data after the JSON value")
	}
	return v, nil
}

func decodeValue(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			o := NewObj()
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, err
				}
				v, err := decodeValue(dec)
				if err != nil {
					return nil, err
				}
				o.Set(kt.(string), v)
			}
			_, err := dec.Token()
			return o, err
		case '[':
			l := []any{}
			for dec.More() {
				v, err := decodeValue(dec)
				if err != nil {
					return nil, err
				}
				l = append(l, v)
			}
			_, err := dec.Token()
			return l, err
		}
		return nil, fmt.Errorf("unexpected %v", t)
	case json.Number:
		s := t.String()
		if !strings.ContainsAny(s, ".eE") {
			if i, err := strconv.ParseInt(s, 10, 64); err == nil {
				return i, nil
			}
		}
		f, err := strconv.ParseFloat(s, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, err
		}
		return f, nil
	default:
		return t, nil // nil, bool, string
	}
}

// jsonText is v as one line of JSON, text as it is (no \u escapes for
// <, > and &, nor for non-ASCII characters).
func jsonText(v any) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		panic(fmt.Sprintf("jsonText: %v", err))
	}
	return strings.TrimSuffix(b.String(), "\n")
}

// pyFloatRepr is Python's repr of a float: the shortest digits that read
// back the same, fixed between 1e-4 and 1e16 (always with a ".0" or a
// fraction), else with an exponent ("1e-05", "1.5e+16").
func pyFloatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "NaN"
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	}
	e := strconv.FormatFloat(f, 'e', -1, 64)
	exp, _ := strconv.Atoi(e[strings.IndexByte(e, 'e')+1:])
	if f == 0 || exp >= -4 && exp < 16 {
		s := strconv.FormatFloat(f, 'f', -1, 64)
		if !strings.Contains(s, ".") {
			s += ".0"
		}
		return s
	}
	return e
}

// strs is a []string as JSON's []any.
func strs(s []string) []any {
	l := make([]any, len(s))
	for i, x := range s {
		l[i] = x
	}
	return l
}

// number is a JSON number as float64 (a bool is not one).
func number(v any) (float64, bool) {
	switch v := v.(type) {
	case int64:
		return float64(v), true
	case float64:
		return v, true
	case int:
		return float64(v), true
	}
	return 0, false
}
