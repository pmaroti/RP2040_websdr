package main

// AM and SSB demodulation of the IF sample stream.
//
// The Pico samples the mixer's IF output: a real signal from 0 Hz to half
// the sample rate (12.5 kHz at 25 kS/s). An RF signal at LO + f or LO - f
// both come out as the IF tone f. The one below the LO comes out with its
// spectrum mirrored, so an upper sideband signal at RF below the LO is a
// lower sideband signal in the IF. resolveTuning() takes care of that.
//
// Signal path per listener, at the input rate unless noted:
//
//	x (real, -1..1)
//	  -> DC block                    remove the ADC's bias
//	  -> * exp(-j 2 pi f_if t)       complex mix: carrier to 0 Hz
//	  -> complex FIR, decimate by D  channel filter, audio rate = rate / D
//	       AM:  -bw/2 .. +bw/2
//	       USB: +lowCut .. +lowCut+bw   (taps are a low-pass shifted up)
//	       LSB: -lowCut-bw .. -lowCut   (shifted down)
//	  -> AM: |y| minus its average (envelope detector)
//	     SSB: Re(y)                  (product detector, carrier at 0 Hz)
//	  -> AGC -> int16 PCM

import (
	"fmt"
	"math"
	"math/cmplx"
)

const (
	ssbLowCut     = 100.0 // Hz between the carrier and the SSB passband
	targetOutRate = 16000.0
	agcTarget     = 0.3    // audio peak level the AGC aims for (full scale = 1)
	agcMaxGain    = 3000.0 // about 70 dB: limits how far noise is pulled up
)

// Demodulation modes.
const (
	ModeOff = "off"
	ModeAM  = "am"
	ModeLSB = "lsb"
	ModeUSB = "usb"
)

func validMode(m string) bool {
	return m == ModeOff || m == ModeAM || m == ModeLSB || m == ModeUSB
}

// defaultBandwidth is used when the page sends none.
func defaultBandwidth(mode string) float64 {
	if mode == ModeAM {
		return 6000
	}
	return 2700
}

// bandwidthLimits returns the bandwidth range that fits the audio rate.
func bandwidthLimits(mode string, outRate float64) (lo, hi float64) {
	if mode == ModeAM {
		// The AM filter is centred on the carrier; audio goes up to bw/2.
		return 1000, math.Floor(0.9*outRate/100) * 100
	}
	return 300, math.Floor((0.45*outRate-ssbLowCut)/100) * 100
}

// decimation picks the decimation factor: the smallest D that divides the
// input rate evenly with rate/D <= 16 kHz, so the audio rate is a whole
// number (25 kS/s -> D = 2, 12.5 kHz audio).
func decimation(inRate int) int {
	d := int(math.Ceil(float64(inRate) / targetOutRate))
	if d < 1 {
		d = 1
	}
	for k := d; k < d*2; k++ {
		if inRate%k == 0 {
			return k
		}
	}
	return d
}

// Tuning is a resolved tuning request.
type Tuning struct {
	IFHz     float64 // carrier frequency in the IF
	IFMode   string  // sideband to select in the IF (swapped if inverted)
	Inverted bool    // RF below the LO: IF spectrum is mirrored
	ViaRF    bool    // freq was an RF frequency (LO on)
}

// resolveTuning turns the requested frequency into an IF frequency. With
// the LO running, freq is an RF frequency and the IF is |freq - LO|. With the
// LO off (or freq clearly an IF value), freq is the IF frequency itself.
func resolveTuning(mode string, freq, lo float64, inRate int) (Tuning, error) {
	nyq := float64(inRate) / 2
	t := Tuning{IFMode: mode}
	if lo > 0 && !(freq <= nyq && math.Abs(freq-lo) > nyq) {
		d := freq - lo
		if d < 0 {
			d, t.Inverted = -d, true
		}
		if d > nyq {
			return t, fmt.Errorf("%s is outside the receiver's range, LO %s ± %s",
				fmtHz(freq), fmtHz(lo), fmtHz(nyq))
		}
		t.IFHz, t.ViaRF = d, true
	} else {
		if freq < 0 || freq > nyq {
			return t, fmt.Errorf("LO is off: enter an IF frequency from 0 to %s", fmtHz(nyq))
		}
		t.IFHz = freq
	}
	if t.Inverted {
		switch mode {
		case ModeUSB:
			t.IFMode = ModeLSB
		case ModeLSB:
			t.IFMode = ModeUSB
		}
	}
	return t, nil
}

// passband returns the IF frequencies the channel filter passes.
func passband(ifMode string, ifHz, bw float64) (lo, hi float64) {
	switch ifMode {
	case ModeUSB:
		return ifHz + ssbLowCut, ifHz + ssbLowCut + bw
	case ModeLSB:
		return ifHz - ssbLowCut - bw, ifHz - ssbLowCut
	default:
		return ifHz - bw/2, ifHz + bw/2
	}
}

func fmtHz(hz float64) string {
	switch a := math.Abs(hz); {
	case a >= 1e6:
		return fmt.Sprintf("%.6g MHz", hz/1e6)
	case a >= 1e3:
		return fmt.Sprintf("%.6g kHz", hz/1e3)
	default:
		return fmt.Sprintf("%.0f Hz", hz)
	}
}

// channelTaps designs the complex channel filter: a Blackman-windowed sinc
// low-pass of half-width bw/2, shifted to the passband centre for SSB.
func channelTaps(ifMode string, bw float64, inRate int) []complex128 {
	fs := float64(inRate)
	// Transition about 400 Hz (Blackman: ~5.5 fs / N), capped for high rates.
	n := int(5.5*fs/400) | 1
	if n > 1023 {
		n = 1023
	}
	if n < 63 {
		n = 63
	}
	m := float64(n-1) / 2
	fc := bw / 2 / fs // half-width as a fraction of the sample rate
	centre := 0.0
	switch ifMode {
	case ModeUSB:
		centre = ssbLowCut + bw/2
	case ModeLSB:
		centre = -ssbLowCut - bw/2
	}
	h := make([]float64, n)
	sum := 0.0
	for k := range h {
		t := float64(k) - m
		s := 2 * fc
		if t != 0 {
			s = math.Sin(2*math.Pi*fc*t) / (math.Pi * t)
		}
		w := 0.42 - 0.5*math.Cos(2*math.Pi*float64(k)/float64(n-1)) + 0.08*math.Cos(4*math.Pi*float64(k)/float64(n-1))
		h[k] = s * w
		sum += h[k]
	}
	taps := make([]complex128, n)
	for k := range h {
		taps[k] = complex(h[k]/sum, 0) * cmplx.Exp(complex(0, 2*math.Pi*centre*(float64(k)-m)/fs))
	}
	return taps
}

// Demodulator turns IF samples into audio for one listener. Not safe for
// concurrent use; each listener owns one.
type Demodulator struct {
	InRate  int
	Decim   int
	OutRate float64

	ifMode string
	ifHz   float64
	bw     float64

	rtaps []complex128 // taps, reversed, for a dot product with the delay line
	hist  []complex128 // delay line, stored twice so a window is contiguous
	pos   int
	phase int // input samples since the last output

	rot, step complex128 // NCO: rot *= step every sample
	rotN      int

	dcIn, dcAlpha  float64 // input DC block
	amAvg, amA     float64 // AM: envelope average (removes the carrier)
	agcPeak        float64
	agcAtt, agcDec float64
	sigPow, sigA   float64 // channel power, for the signal meter
}

func NewDemodulator(inRate int) *Demodulator {
	d := &Demodulator{InRate: inRate, Decim: decimation(inRate)}
	d.OutRate = float64(inRate) / float64(d.Decim)
	fs, fo := float64(inRate), d.OutRate
	d.dcAlpha = 1 - math.Exp(-2*math.Pi*2/fs)
	d.amA = 1 - math.Exp(-2*math.Pi*20/fo)
	d.agcAtt = 1 - math.Exp(-1/(0.002*fo))
	d.agcDec = 1 - math.Exp(-1/(0.6*fo))
	d.sigA = 1 - math.Exp(-1/(0.3*fo))
	d.rot = 1
	return d
}

// Configure sets the IF mode (am, usb or lsb as seen in the IF), carrier
// frequency and bandwidth. Filter state is kept so retuning doesn't click
// more than it has to.
func (d *Demodulator) Configure(ifMode string, ifHz, bw float64) {
	if ifMode != d.ifMode || bw != d.bw || d.rtaps == nil {
		taps := channelTaps(ifMode, bw, d.InRate)
		n := len(taps)
		d.rtaps = make([]complex128, n)
		for k := range taps {
			d.rtaps[n-1-k] = taps[k]
		}
		if len(d.hist) != 2*n {
			d.hist, d.pos = make([]complex128, 2*n), 0
		}
	}
	d.ifMode, d.ifHz, d.bw = ifMode, ifHz, bw
	d.step = cmplx.Exp(complex(0, -2*math.Pi*ifHz/float64(d.InRate)))
}

// SignalDB is the channel power in dBFS (a full-scale carrier reads 0).
func (d *Demodulator) SignalDB() float64 {
	return 10 * math.Log10(4*d.sigPow+1e-15)
}

// Process demodulates one block of input samples and returns the audio.
func (d *Demodulator) Process(x []float32) []int16 {
	n := len(d.rtaps)
	out := make([]int16, 0, len(x)/d.Decim+1)
	for _, v := range x {
		s := float64(v)
		d.dcIn += d.dcAlpha * (s - d.dcIn)
		s -= d.dcIn
		z := complex(s, 0) * d.rot
		d.rot *= d.step
		if d.rotN++; d.rotN == 1024 { // keep |rot| = 1 against rounding drift
			d.rot /= complex(cmplx.Abs(d.rot), 0)
			d.rotN = 0
		}
		d.hist[d.pos], d.hist[d.pos+n] = z, z
		d.pos = (d.pos + 1) % n
		if d.phase++; d.phase < d.Decim {
			continue
		}
		d.phase = 0
		// Window of the newest n samples, oldest first.
		win := d.hist[d.pos : d.pos+n]
		var yr, yi float64
		for k, t := range d.rtaps {
			w := win[k]
			yr += real(t)*real(w) - imag(t)*imag(w)
			yi += real(t)*imag(w) + imag(t)*real(w)
		}
		p := yr*yr + yi*yi
		d.sigPow += d.sigA * (p - d.sigPow)

		var a float64
		if d.ifMode == ModeAM {
			env := math.Sqrt(p)
			d.amAvg += d.amA * (env - d.amAvg)
			a = env - d.amAvg
		} else {
			a = yr
		}
		// AGC: fast attack, slow release, gain limited to agcMaxGain.
		m := math.Abs(a)
		if m > d.agcPeak {
			d.agcPeak += d.agcAtt * (m - d.agcPeak)
		} else {
			d.agcPeak += d.agcDec * (m - d.agcPeak)
		}
		g := agcTarget / math.Max(d.agcPeak, agcTarget/agcMaxGain)
		o := math.Max(-1, math.Min(1, a*g))
		out = append(out, int16(math.Round(o*32767)))
	}
	return out
}
