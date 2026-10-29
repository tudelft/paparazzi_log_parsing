function y = lowpass(x, fc, fs)
%LOWPASS Zero-phase second-order Butterworth low-pass of the columns of x (no toolbox needed).
%   y = pprz.lowpass(x, fc, fs) with cut-off fc and sample rate fs in Hz; x is sampled uniformly.
    if fc >= fs / 2
        y = x;
        return
    end
    k = tan(pi * fc / fs);
    n = 1 / (1 + sqrt(2) * k + k ^ 2);
    b = [k ^ 2, 2 * k ^ 2, k ^ 2] * n;
    a = [1, 2 * (k ^ 2 - 1) * n, (1 - sqrt(2) * k + k ^ 2) * n];
    y = pass(b, a, x);
    y = flipud(pass(b, a, flipud(y)));
end

function y = pass(b, a, x)
    % Start at rest on the first sample, so there is no step at the edges.
    x0 = x(1, :);
    y = filter(b, a, x - x0) + x0;
end
