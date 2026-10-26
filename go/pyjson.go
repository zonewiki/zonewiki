// JSON as Python's json module reads and writes it: objects keep their keys'
// order (a dict's), numbers stay ints or floats, and dumps writes what
// json.dumps(v, indent=2, ensure_ascii=False) does, byte for byte. The deck's
// files (_deck/*.json) are written so: the same key order and formatting as
// Python, or every write would be a noisy git diff.
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

// MarshalJSON writes the keys in order (Go's encoder for API answers).
func (o *Obj) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, _ := json.Marshal(k)
		b.Write(kb)
		b.WriteByte(':')
		vb, err := json.Marshal(jsonable(o.m[k]))
		if err != nil {
			return nil, err
		}
		b.Write(vb)
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

// dumps is json.dumps(v, indent=2, ensure_ascii=False).
func dumps(v any) string {
	var b strings.Builder
	dumpValue(&b, v, 0)
	return b.String()
}

func dumpValue(b *strings.Builder, v any, depth int) {
	switch v := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if v {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		dumpString(b, v)
	case int:
		b.WriteString(strconv.Itoa(v))
	case int64:
		b.WriteString(strconv.FormatInt(v, 10))
	case float64:
		b.WriteString(pyFloatRepr(v))
	case []string:
		l := make([]any, len(v))
		for i, s := range v {
			l[i] = s
		}
		dumpValue(b, l, depth)
	case []any:
		if len(v) == 0 {
			b.WriteString("[]")
			return
		}
		b.WriteString("[")
		for i, item := range v {
			if i > 0 {
				b.WriteString(",")
			}
			newline(b, depth+1)
			dumpValue(b, item, depth+1)
		}
		newline(b, depth)
		b.WriteString("]")
	case *Obj:
		if v.Len() == 0 {
			b.WriteString("{}")
			return
		}
		b.WriteString("{")
		for i, k := range v.keys {
			if i > 0 {
				b.WriteString(",")
			}
			newline(b, depth+1)
			dumpString(b, k)
			b.WriteString(": ")
			dumpValue(b, v.m[k], depth+1)
		}
		newline(b, depth)
		b.WriteString("}")
	default:
		panic(fmt.Sprintf("dumps: %T", v))
	}
}

func newline(b *strings.Builder, depth int) {
	b.WriteByte('\n')
	for range depth {
		b.WriteString("  ")
	}
}

// dumpString escapes as json.dumps with ensure_ascii=False: only the quote,
// the backslash and control characters.
func dumpString(b *strings.Builder, s string) {
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
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

// dumpsLine is json.dumps(v) with its defaults: one line, ", " and ": "
// between items, every non-ASCII character escaped.
func dumpsLine(v any) string {
	var b strings.Builder
	dumpLine(&b, v, true)
	return b.String()
}

// dumpsLineUTF8 is json.dumps(v, ensure_ascii=False): one line, non-ASCII
// characters as they are.
func dumpsLineUTF8(v any) string {
	var b strings.Builder
	dumpLine(&b, v, false)
	return b.String()
}

func dumpLine(b *strings.Builder, v any, ascii bool) {
	switch v := v.(type) {
	case string:
		if !ascii {
			dumpString(b, v)
			return
		}
		b.WriteByte('"')
		b.WriteString(dumpsInlineString(v))
		b.WriteByte('"')
	case []string:
		l := make([]any, len(v))
		for i, s := range v {
			l[i] = s
		}
		dumpLine(b, l, ascii)
	case []any:
		b.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				b.WriteString(", ")
			}
			dumpLine(b, item, ascii)
		}
		b.WriteByte(']')
	case *Obj:
		b.WriteByte('{')
		for i, k := range v.keys {
			if i > 0 {
				b.WriteString(", ")
			}
			dumpLine(b, k, ascii)
			b.WriteString(": ")
			dumpLine(b, v.m[k], ascii)
		}
		b.WriteByte('}')
	default:
		dumpValue(b, v, 0)
	}
}

// dumpsInlineString: a string's JSON escapes with ensure_ascii (no quotes).
func dumpsInlineString(s string) string {
	var b strings.Builder
	var tmp strings.Builder
	dumpString(&tmp, s)
	inner := tmp.String()
	for _, r := range inner[1 : len(inner)-1] {
		switch {
		case r < 0x80:
			b.WriteRune(r)
		case r < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			r -= 0x10000
			fmt.Fprintf(&b, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
		}
	}
	return b.String()
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

// pyEqual is Python's == on JSON values (1 == 1.0, dicts regardless of key
// order).
func pyEqual(a, b any) bool {
	switch a := a.(type) {
	case nil:
		return b == nil
	case bool:
		bb, ok := b.(bool)
		return ok && a == bb
	case string:
		bs, ok := b.(string)
		return ok && a == bs
	case int64, float64:
		af, aok := number(a)
		bf, bok := number(b)
		return aok && bok && af == bf
	case []any:
		bl, ok := b.([]any)
		if !ok || len(a) != len(bl) {
			return false
		}
		for i := range a {
			if !pyEqual(a[i], bl[i]) {
				return false
			}
		}
		return true
	case *Obj:
		bo, ok := b.(*Obj)
		if !ok || a.Len() != bo.Len() {
			return false
		}
		for _, k := range a.keys {
			bv, ok := bo.m[k]
			if !ok || !pyEqual(a.m[k], bv) {
				return false
			}
		}
		return true
	}
	return false
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
