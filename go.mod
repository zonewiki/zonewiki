// Zonewiki's own files, built into the binary (zonefiles.go): a module of its
// own at the repository's root, since go:embed reaches only below its
// package's folder. The server is the module in go/, which takes this one
// with a replace directive (go/go.mod).
module zonefiles

go 1.27.3
