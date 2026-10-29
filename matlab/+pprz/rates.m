function [t, pqr, source] = rates(d)
%RATES Body rates p, q, r in rad/s from IMU_GYRO, IMU_GYRO_SCALED (first gyro) or BODY_RATES_ACCEL.
%   [t, pqr, source] = pprz.rates(f.data); pqr is N×3. Empty when neither was logged.
    t = zeros(0, 1);
    pqr = zeros(0, 3);
    source = '';
    gyro = '';
    if pprz.has(d, 'IMU_GYRO')
        gyro = 'IMU_GYRO';
    elseif pprz.has(d, 'IMU_GYRO_SCALED')
        gyro = 'IMU_GYRO_SCALED';
    end
    if ~isempty(gyro)
        m = d.(gyro);
        rows = true(size(m.t));
        if isfield(m, 'id')
            ids = unique(m.id);
            rows = m.id == ids(1);
        end
        t = m.t(rows);
        pqr = deg2rad(double([m.gp(rows), m.gq(rows), m.gr(rows)]));  % exported in deg/s
        source = gyro;
    elseif pprz.has(d, 'BODY_RATES_ACCEL')
        m = d.BODY_RATES_ACCEL;
        t = m.t;
        pqr = double([m.p, m.q, m.r]);
        source = 'BODY_RATES_ACCEL';
    end
    [t, keep] = unique(t);
    pqr = pqr(keep, :);
end
