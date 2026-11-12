// A client that stops sending or reading doesn't hold its connection
// (docs/spec/security.md, Slow clients): Zone serves at most 100 at a
// time, so a hundred that sent half a request and then nothing would be
// all of them.
package main

import (
	"io"
	"net/http"
	"time"
)

// stallTime: how long a request's body or a response may stand still. Not
// a limit on the whole request: an upload over a slow line takes as long
// as it takes, as long as it moves.
var stallTime = 60 * time.Second

// noStalls gives every read of the request's body and every write of the
// response stallTime to get somewhere; one that doesn't fails, and the
// server closes the connection. What a route does in between (a search, a
// git command) isn't timed.
func noStalls(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rc := http.NewResponseController(w)
		// The connection may be an earlier request's, with its last
		// write's deadline, long past.
		_ = rc.SetWriteDeadline(time.Time{})
		if r.Body != nil && r.Body != http.NoBody {
			// From now, not from the first read: a body a route never
			// reads (a refused request) is read by the server after it.
			_ = rc.SetReadDeadline(time.Now().Add(stallTime))
			r.Body = &steadyBody{ReadCloser: r.Body, rc: rc}
		}
		next.ServeHTTP(&steadyWriter{ResponseWriter: w, rc: rc}, r)
	})
}

// steadyBody: a request's body, each read with its own deadline.
type steadyBody struct {
	io.ReadCloser
	rc *http.ResponseController
}

func (b *steadyBody) Read(p []byte) (int, error) {
	_ = b.rc.SetReadDeadline(time.Now().Add(stallTime))
	n, err := b.ReadCloser.Read(p)
	if err == io.EOF {
		// All read: from here the server only watches the connection
		// for the client going away, however long the route takes. (A
		// read that failed keeps its deadline: nothing more is read.)
		_ = b.rc.SetReadDeadline(time.Time{})
	}
	return n, err
}

// steadyWriter: a response, each write with its own deadline.
type steadyWriter struct {
	http.ResponseWriter
	rc *http.ResponseController
}

func (s *steadyWriter) WriteHeader(status int) {
	_ = s.rc.SetWriteDeadline(time.Now().Add(stallTime))
	s.ResponseWriter.WriteHeader(status)
}

func (s *steadyWriter) Write(b []byte) (int, error) {
	_ = s.rc.SetWriteDeadline(time.Now().Add(stallTime))
	return s.ResponseWriter.Write(b)
}

func (s *steadyWriter) Unwrap() http.ResponseWriter { return s.ResponseWriter }
