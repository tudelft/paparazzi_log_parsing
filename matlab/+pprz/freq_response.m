function r = freq_response(u, y, fs, n)
%FREQ_RESPONSE Frequency response from u to y by Welch averaging (Hann windows, half overlap).
%   r = pprz.freq_response(u, y, fs, n): u and y sampled uniformly at fs Hz, segments of n samples.
%   r has f [Hz], H (complex, H1 estimate), gain_db, phase_deg and coherence (0..1).
    u = double(u(:));
    y = double(y(:));
    n = min(n, numel(u));
    w = 0.5 - 0.5 * cos(2 * pi * (0:n - 1)' / (n - 1));
    step = max(1, floor(n / 2));
    m = floor(n / 2) + 1;
    [puu, pyy, puy] = deal(zeros(m, 1));
    for s = 1:step:numel(u) - n + 1
        k = s:s + n - 1;
        U = fft(w .* (u(k) - mean(u(k))));
        Y = fft(w .* (y(k) - mean(y(k))));
        puu = puu + abs(U(1:m)) .^ 2;
        pyy = pyy + abs(Y(1:m)) .^ 2;
        puy = puy + conj(U(1:m)) .* Y(1:m);
    end
    r.f = (0:m - 1)' * fs / n;
    r.H = puy ./ puu;
    r.gain_db = 20 * log10(abs(r.H));
    r.phase_deg = rad2deg(unwrap(angle(r.H)));
    r.coherence = abs(puy) .^ 2 ./ (puu .* pyy);
end
