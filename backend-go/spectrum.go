package main

import (
	"math"
	"math/bits"
	"math/cmplx"
	"sync"
)

// FFT sizes the page may choose from. Larger = finer resolution
// (sample_rate / size Hz per bin) but slower to react.
var fftSizes = []int{512, 1024, 2048, 4096, 8192, 16384}

// fft computes the discrete Fourier transform of a in place (radix-2,
// len(a) must be a power of two).
func fft(a []complex128) {
	n := len(a)
	shift := 64 - uint(bits.TrailingZeros(uint(n)))
	for i := 0; i < n; i++ { // bit-reversal permutation
		j := int(bits.Reverse64(uint64(i)) >> shift)
		if j > i {
			a[i], a[j] = a[j], a[i]
		}
	}
	for size := 2; size <= n; size <<= 1 {
		w := cmplx.Exp(complex(0, -2*math.Pi/float64(size)))
		for start := 0; start < n; start += size {
			wk := complex(1, 0)
			for k := 0; k < size/2; k++ {
				u, v := a[start+k], a[start+k+size/2]*wk
				a[start+k], a[start+k+size/2] = u+v, u-v
				wk *= w
			}
		}
	}
}

// Spectrum is a Hann-windowed FFT with optional exponential averaging, in
// dBFS (a full-scale sine reads 0 dBFS). Safe for concurrent use.
type Spectrum struct {
	mu        sync.Mutex
	fftSize   int
	averaging float64 // 0 = none, closer to 1 = smoother
	avg       []float64
	window    []float64
	wsum      float64
}

func NewSpectrum() *Spectrum { return &Spectrum{fftSize: 4096, averaging: 0.5} }

func (s *Spectrum) Settings() (int, float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.fftSize, s.averaging
}

// SetFFTSize accepts only the offered sizes, and restarts averaging since
// the old average has a different number of bins.
func (s *Spectrum) SetFFTSize(n int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, v := range fftSizes {
		if v == n && n != s.fftSize {
			s.fftSize, s.avg = n, nil
		}
	}
}

func (s *Spectrum) SetAveraging(a float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	// 0.98 is already very slow; 1.0 would freeze the display.
	s.averaging = math.Min(math.Max(a, 0), 0.98)
}

// Compute returns the spectrum of x in dBFS, one value per bin from 0 Hz to
// sample_rate / 2 (len(x)/2 + 1 values).
func (s *Spectrum) Compute(x []float32) []float32 {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := len(x)
	if len(s.window) != n {
		// Hann window, numpy.hanning's definition.
		s.window, s.wsum, s.avg = make([]float64, n), 0, nil
		for i := range s.window {
			s.window[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/float64(n-1))
			s.wsum += s.window[i]
		}
	}
	mean := 0.0
	for _, v := range x {
		mean += float64(v)
	}
	mean /= float64(n)
	a := make([]complex128, n)
	for i, v := range x {
		a[i] = complex((float64(v)-mean)*s.window[i], 0)
	}
	fft(a)
	// A sine of amplitude A gives |X| = A * sum(w) / 2, so scaling power by
	// (2 / sum(w))^2 makes a full-scale sine 0 dBFS.
	scale := math.Pow(2/s.wsum, 2)
	bins := n/2 + 1
	if len(s.avg) != bins || s.averaging <= 0 {
		s.avg = make([]float64, bins)
		for i := range s.avg {
			s.avg[i] = -1 // marker: no average yet
		}
	}
	out := make([]float32, bins)
	for i := 0; i < bins; i++ {
		p := (real(a[i])*real(a[i]) + imag(a[i])*imag(a[i])) * scale
		if s.avg[i] < 0 || s.averaging <= 0 {
			s.avg[i] = p
		} else {
			s.avg[i] = s.averaging*s.avg[i] + (1-s.averaging)*p
		}
		out[i] = float32(10 * math.Log10(s.avg[i]+1e-12))
	}
	return out
}
