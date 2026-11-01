package main

import (
	"encoding/binary"
	"math"
	"math/cmplx"
	"testing"
)

const testRate = 25000

// toneLevel measures the amplitude of frequency f in pcm (Goertzel-style
// single-bin DFT), skipping the first part while filters and AGC settle.
func toneLevel(pcm []int16, rate, f float64) float64 {
	skip := len(pcm) / 2
	var acc complex128
	for i, v := range pcm[skip:] {
		acc += complex(float64(v)/32768, 0) * cmplx.Exp(complex(0, -2*math.Pi*f*float64(i)/rate))
	}
	return 2 * cmplx.Abs(acc) / float64(len(pcm)-skip)
}

func rms(pcm []int16) float64 {
	s := 0.0
	for _, v := range pcm[len(pcm)/2:] {
		s += math.Pow(float64(v)/32768, 2)
	}
	return math.Sqrt(s / float64(len(pcm)-len(pcm)/2))
}

func run(d *Demodulator, sig func(t float64) float64, seconds float64) []int16 {
	var out []int16
	n := int(seconds * testRate)
	for start := 0; start < n; start += 500 {
		x := make([]float32, 500)
		for i := range x {
			x[i] = float32(sig(float64(start+i) / testRate))
		}
		out = append(out, d.Process(x)...)
	}
	return out
}

func TestDecimation(t *testing.T) {
	for in, want := range map[int]int{25000: 2, 50000: 4, 500000: 32, 8000: 1} {
		if got := decimation(in); got != want {
			t.Errorf("decimation(%d) = %d, want %d", in, got, want)
		}
	}
}

func TestUSBRecoversToneAndRejectsLSB(t *testing.T) {
	// Carrier at 6 kHz, tone 1 kHz above it (USB) plus a 10x weaker one 1.5 kHz below (LSB).
	sig := func(t float64) float64 {
		return 0.1*math.Sin(2*math.Pi*7000*t) + 0.01*math.Sin(2*math.Pi*4500*t)
	}
	d := NewDemodulator(testRate)
	d.Configure(ModeUSB, 6000, 2700)
	pcm := run(d, sig, 1.5)
	usb, lsb := toneLevel(pcm, d.OutRate, 1000), toneLevel(pcm, d.OutRate, 1500)
	if usb < 0.2 {
		t.Fatalf("USB tone level %.3f, want about %.1f (AGC target)", usb, agcTarget)
	}
	if r := 20 * math.Log10(lsb/usb); r > -55 {
		t.Errorf("opposite sideband only %.1f dB down", r)
	}

	d = NewDemodulator(testRate)
	d.Configure(ModeLSB, 6000, 2700)
	pcm = run(d, sig, 1.5)
	usb, lsb = toneLevel(pcm, d.OutRate, 1000), toneLevel(pcm, d.OutRate, 1500)
	if lsb < 0.2 || 20*math.Log10(usb/lsb) > -30 {
		t.Errorf("LSB: wanted 1.5 kHz %.3f, unwanted 1 kHz %.3f", lsb, usb)
	}
}

func TestAMRecoversModulation(t *testing.T) {
	// 4 kHz carrier, 50 % modulated with 400 Hz, plus a strong carrier
	// 5 kHz away that the 6 kHz filter must remove.
	sig := func(t float64) float64 {
		return 0.2*math.Sin(2*math.Pi*4000*t)*(1+0.5*math.Sin(2*math.Pi*400*t)) +
			0.3*math.Sin(2*math.Pi*9000*t)
	}
	d := NewDemodulator(testRate)
	d.Configure(ModeAM, 4000, 6000)
	pcm := run(d, sig, 1.5)
	tone := toneLevel(pcm, d.OutRate, 400)
	if tone < 0.15 {
		t.Fatalf("400 Hz level %.3f", tone)
	}
	// Everything that is not the 400 Hz tone (harmonics, leakage) should be small.
	total := rms(pcm) * math.Sqrt2
	if other := math.Sqrt(math.Max(0, total*total-tone*tone)); other/tone > 0.05 {
		t.Errorf("distortion/leakage %.1f %%", 100*other/tone)
	}
	// Signal meter: carrier amplitude 0.2 -> -14 dBFS.
	if db := d.SignalDB(); math.Abs(db-20*math.Log10(0.2)) > 1.5 {
		t.Errorf("signal meter %.1f dBFS, want %.1f", db, 20*math.Log10(0.2))
	}
}

func TestResolveTuning(t *testing.T) {
	lo := 7.1e6
	cases := []struct {
		mode, wantMode string
		freq, wantIF   float64
		inverted, rf   bool
	}{
		{ModeUSB, ModeUSB, 7.104e6, 4000, false, true},
		{ModeUSB, ModeLSB, 7.096e6, 4000, true, true}, // below LO: mirrored
		{ModeLSB, ModeUSB, 7.095e6, 5000, true, true},
		{ModeAM, ModeAM, 7.09e6, 10000, true, true},
		{ModeUSB, ModeUSB, 3000, 3000, false, false}, // small value: taken as IF
	}
	for _, c := range cases {
		got, err := resolveTuning(c.mode, c.freq, lo, testRate)
		if err != nil {
			t.Fatalf("%v: %v", c, err)
		}
		if got.IFHz != c.wantIF || got.IFMode != c.wantMode || got.Inverted != c.inverted || got.ViaRF != c.rf {
			t.Errorf("resolveTuning(%s, %.0f) = %+v", c.mode, c.freq, got)
		}
	}
	if _, err := resolveTuning(ModeAM, 7.2e6, lo, testRate); err == nil {
		t.Error("100 kHz off the LO should be out of range")
	}
	if _, err := resolveTuning(ModeAM, 7.2e6, 0, testRate); err == nil {
		t.Error("RF frequency with the LO off should be an error")
	}
}

// End to end through the sideband swap: an USB signal 3 kHz below the LO
// shows up mirrored in the IF and still demodulates to the right tone.
func TestUSBBelowLO(t *testing.T) {
	lo := 7.1e6
	rfCarrier, audio := 7.097e6, 800.0
	// RF tone at carrier + 800 Hz = LO - 2200 Hz -> IF 2200 Hz.
	ifTone := lo - (rfCarrier + audio)
	sig := func(t float64) float64 { return 0.1 * math.Sin(2*math.Pi*ifTone*t) }
	tun, err := resolveTuning(ModeUSB, rfCarrier, lo, testRate)
	if err != nil {
		t.Fatal(err)
	}
	d := NewDemodulator(testRate)
	d.Configure(tun.IFMode, tun.IFHz, 2700)
	pcm := run(d, sig, 1.5)
	if l := toneLevel(pcm, d.OutRate, audio); l < 0.2 {
		t.Errorf("800 Hz level %.3f", l)
	}
}

func TestFFTMatchesDFT(t *testing.T) {
	n := 64
	a := make([]complex128, n)
	for i := range a {
		a[i] = complex(math.Sin(float64(i)*0.7)+0.3*float64(i%5), math.Cos(float64(i)))
	}
	want := make([]complex128, n)
	for k := range want {
		for i, v := range a {
			want[k] += v * cmplx.Exp(complex(0, -2*math.Pi*float64(k*i)/float64(n)))
		}
	}
	fft(a)
	for k := range a {
		if cmplx.Abs(a[k]-want[k]) > 1e-9 {
			t.Fatalf("bin %d: %v, want %v", k, a[k], want[k])
		}
	}
}

func TestSpectrumFullScaleSine(t *testing.T) {
	n := 4096
	x := make([]float32, n)
	f := 1000.0 / testRate * float64(n) // put it exactly on a bin
	f = math.Round(f)
	for i := range x {
		x[i] = float32(math.Sin(2 * math.Pi * f * float64(i) / float64(n)))
	}
	s := NewSpectrum()
	db := s.Compute(x)
	if len(db) != n/2+1 || math.Abs(float64(db[int(f)])) > 0.1 {
		t.Errorf("peak %.2f dBFS (want 0), %d bins", db[int(f)], len(db))
	}
}

func TestParseDatagram(t *testing.T) {
	p := make([]byte, 16+2*3)
	binary.LittleEndian.PutUint32(p, packetMagic)
	binary.LittleEndian.PutUint32(p[4:], 42)
	binary.LittleEndian.PutUint32(p[8:], 25000)
	binary.LittleEndian.PutUint16(p[12:], 3)
	binary.LittleEndian.PutUint16(p[14:], flagOverrun)
	for i, c := range []uint16{0, 2048, 4095} {
		binary.LittleEndian.PutUint16(p[16+2*i:], c)
	}
	seq, rate, flags, x, ok := parseDatagram(p)
	if !ok || seq != 42 || rate != 25000 || flags != flagOverrun || len(x) != 3 || x[0] != -1 || x[1] != 0 {
		t.Fatalf("got %v %v %v %v %v", seq, rate, flags, x, ok)
	}
	if _, _, _, _, ok := parseDatagram(p[:len(p)-1]); ok {
		t.Error("short datagram accepted")
	}
}

func TestParseFreq(t *testing.T) {
	for in, want := range map[string]float64{"7.1M": 7.1e6, "7100 kHz": 7.1e6, "7100000": 7.1e6, "off": 0, "1.5k": 1500} {
		if got, err := parseSquareWave(in); err != nil || got != want {
			t.Errorf("%q: %v %v", in, got, err)
		}
	}
	if _, err := parseSquareWave("100M"); err == nil {
		t.Error("100M accepted")
	}
	if got := simulatedPicoFreq(7.1e6); math.Abs(got-7098491.571) > 0.01 {
		t.Errorf("simulated 7.1M = %.3f", got)
	}
}
