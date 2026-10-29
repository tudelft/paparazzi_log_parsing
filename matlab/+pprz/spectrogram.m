function [P, freq, t] = spectrogram(tx, x, n)
%SPECTROGRAM Power spectral density over time of the columns of x, summed over the columns (no toolbox).
%   [P, freq, t] = pprz.spectrogram(tx, x, n): x (N×k) at times tx is resampled uniformly at its median
%   rate; Hann windows of n samples (default about 1 s, a power of 2) overlap by half. P is
%   numel(freq)×numel(t) in (unit)²/Hz, t the window centres.
    tx = tx(:);
    [tx, keep] = unique(tx);
    x = double(x(keep, :));
    fs = 1 / median(diff(tx));
    if nargin < 3
        n = 2 ^ nextpow2(max(64, fs));
    end
    tu = (tx(1):1 / fs:tx(end))';
    xu = interp1(tx, x, tu);
    n = min(n, numel(tu));
    w = 0.5 - 0.5 * cos(2 * pi * (0:n - 1)' / (n - 1));
    m = floor(n / 2) + 1;
    starts = 1:floor(n / 2):numel(tu) - n + 1;
    P = zeros(m, numel(starts));
    for s = 1:numel(starts)
        seg = xu(starts(s):starts(s) + n - 1, :);
        X = fft(w .* (seg - mean(seg, 1)));
        P(:, s) = sum(abs(X(1:m, :)) .^ 2, 2);
    end
    P = P / (fs * sum(w .^ 2));
    P(2:end - 1, :) = 2 * P(2:end - 1, :);  % one-sided
    freq = (0:m - 1)' * fs / n;
    t = tu(starts + floor(n / 2));
end
