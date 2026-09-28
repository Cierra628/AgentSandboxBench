// Run from the pinned template-manager module to test its exact reflink dependency.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/KarpelesLab/reflink"
)

func main() {
	if len(os.Args) != 2 {
		panic("expected a new evidence directory")
	}
	dir := os.Args[1]
	if err := os.Mkdir(dir, 0700); err != nil {
		panic(err)
	}
	src := filepath.Join(dir, "source.img")
	f, err := os.Create(src)
	if err != nil {
		panic(err)
	}
	const size int64 = (2 << 30) + (2 << 20)
	marker := []byte("ASB_COPY_TAIL")
	if err = f.Truncate(size); err != nil {
		panic(err)
	}
	if _, err = f.WriteAt(marker, size-int64(len(marker))); err != nil {
		panic(err)
	}
	f.Close()
	results := map[string]interface{}{"source_size": size}
	for _, name := range []string{"reflink_auto", "cp_auto"} {
		dst := filepath.Join(dir, name+".img")
		if name == "reflink_auto" {
			err = reflink.Auto(src, dst)
		} else {
			err = exec.Command("cp", "--reflink=auto", "--sparse=always", src, dst).Run()
		}
		row := map[string]interface{}{"error": fmt.Sprint(err)}
		if st, e := os.Stat(dst); e == nil {
			row["size"] = st.Size()
			d, e := os.Open(dst)
			if e != nil {
				panic(e)
			}
			buf := make([]byte, len(marker))
			n, _ := d.ReadAt(buf, size-int64(len(marker)))
			d.Close()
			row["tail_matches"] = n == len(marker) && string(buf) == string(marker)
		}
		results[name] = row
	}
	b, _ := json.MarshalIndent(results, "", "  ")
	if err = os.WriteFile(filepath.Join(dir, "result.json"), b, 0600); err != nil {
		panic(err)
	}
	fmt.Println(string(b))
}
