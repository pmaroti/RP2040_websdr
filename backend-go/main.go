// Command pico-websdr is the Go backend for pico-adc-udp: it receives the
// Pico's UDP sample stream, serves the live spectrum page, keeps the Pico's
// square wave (the receiver's LO) at the requested frequency, and
// demodulates AM / LSB / USB into audio streamed to the page.
//
//	pico-websdr              listen for the Pico, serve http://localhost:8080
//	pico-websdr --demo       no Pico needed, generates a test signal
//
// Architecture:
//
//	Pico --UDP 7778--> receiveUDP --> SampleBuffer (ring + subscribers)
//	                                     |                 |
//	                         SpectrumLoop, 10x/s     one Demodulator per
//	                         FFT -> /ws (binary)     /ws/audio listener
//	                                     |                 |
//	Browser <--------- /ws ------------- + --- /ws/audio --+
//	   |
//	   +-- {"squarewave": ...} --> SquareWave --TCP 7779--> Pico
package main

import (
	"context"
	"embed"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"
)

//go:embed static
var embedded embed.FS

// Args are the command line options. Network defaults match the firmware,
// and the names match the Python backend's.
type Args struct {
	HTTPPort    int
	Host        string
	UDPPort     int
	Device      string
	ControlPort int
	FreqPort    int
	Freq        *float64 // nil = keep whatever the Pico has
	FPS         float64
	Demo        bool
	StaticDir   string
}

// envDefault lets every option also be set as PICO_<NAME> in the
// environment (handy in docker-compose); the flag wins if both are given.
func envDefault(name, def string) string {
	if v, ok := os.LookupEnv("PICO_" + strings.ToUpper(strings.ReplaceAll(name, "-", "_"))); ok {
		return v
	}
	return def
}

func envInt(name string, def int) int {
	v, err := strconv.Atoi(envDefault(name, strconv.Itoa(def)))
	if err != nil {
		return def
	}
	return v
}

func parseArgs() Args {
	var a Args
	var freq string
	fl := flag.CommandLine
	fl.IntVar(&a.HTTPPort, "http-port", envInt("http-port", 8080), "web page port")
	fl.StringVar(&a.Host, "host", envDefault("host", "0.0.0.0"), "address to serve on (0.0.0.0 = all interfaces)")
	fl.IntVar(&a.UDPPort, "udp-port", envInt("udp-port", 7778), "UDP port for samples")
	fl.StringVar(&a.Device, "device", envDefault("device", "192.168.7.1"), "Pico IP address")
	fl.IntVar(&a.ControlPort, "control-port", envInt("control-port", 7777), "Pico control port")
	fl.IntVar(&a.FreqPort, "freq-port", envInt("freq-port", 7779), "Pico square wave TCP port")
	fl.StringVar(&freq, "freq", envDefault("freq", "7.1M"),
		"square wave (LO) frequency to set at startup, e.g. 7.1M, 10k, off, or 'keep' to leave the Pico as it is")
	fps, _ := strconv.ParseFloat(envDefault("fps", "10"), 64)
	fl.Float64Var(&a.FPS, "fps", fps, "spectrum updates per second")
	demo, _ := strconv.ParseBool(envDefault("demo", "false"))
	fl.BoolVar(&a.Demo, "demo", demo, "generate a test signal instead of listening for the Pico")
	fl.StringVar(&a.StaticDir, "static", envDefault("static", ""), "serve the page from this folder instead of the built-in copy (for editing index.html)")
	flag.Parse()

	if a.FPS <= 0 || a.FPS > 60 {
		fmt.Fprintln(os.Stderr, "--fps: must be between 0 and 60")
		os.Exit(2)
	}
	// Turn --freq into Hz now, so a typo stops the program with a clear
	// message instead of failing later.
	if strings.ToLower(freq) != "keep" {
		hz, err := parseSquareWave(freq)
		if err != nil {
			fmt.Fprintf(os.Stderr, "--freq: %v\n", err)
			os.Exit(2)
		}
		a.Freq = &hz
	}
	return a
}

func main() {
	log.SetFlags(log.Ltime)
	args := parseArgs()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	static, _ := fs.Sub(embedded, "static")
	if args.StaticDir != "" {
		static = os.DirFS(args.StaticDir)
	}

	buf := NewSampleBuffer()
	srv := NewServer(args, buf, static)
	go srv.SpectrumLoop(ctx)
	go srv.sqw.Run(ctx)

	if args.Demo {
		log.Print("demo mode: generating a test signal")
		go demoSource(ctx, buf, 25000, 500)
	} else {
		conn, err := listenUDP(args.UDPPort)
		if err != nil {
			log.Fatalf("UDP port %d: %v (is host/receive.py or the Python backend running?)", args.UDPPort, err)
		}
		pico := &net.UDPAddr{IP: net.ParseIP(args.Device), Port: args.ControlPort}
		if pico.IP == nil {
			ips, err := net.LookupIP(args.Device)
			if err != nil || len(ips) == 0 {
				log.Fatalf("--device %s: %v", args.Device, err)
			}
			pico.IP = ips[0]
		}
		log.Printf("Pico at %s", args.Device)
		go receiveUDP(ctx, conn, buf)
		go subscribe(ctx, conn, pico, buf)
	}

	hs := &http.Server{
		Addr:              net.JoinHostPort(args.Host, strconv.Itoa(args.HTTPPort)),
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = hs.Shutdown(sctx)
	}()
	log.Printf("open http://localhost:%d in a browser", args.HTTPPort)
	if err := hs.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
