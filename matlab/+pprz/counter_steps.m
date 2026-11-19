function d = counter_steps(c, wrap)
%COUNTER_STEPS Increase of a wrapping counter from one sample to the next (first sample 0).
%   d = pprz.counter_steps(m.uplink_err, 256)   uint8 counter; uint16: 65536
%   A decrease is taken as a wrap; after a restart of the sender (counter back near 0) the step can be too large,
%   so steps over half the range are NaN.
    c = double(c(:));
    d = [0; mod(diff(c), wrap)];
    d(d > wrap / 2) = NaN;
end
