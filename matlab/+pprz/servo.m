function s = servo(d)
%SERVO Servo telemetry, one row per sample per servo: s = pprz.servo(f.data)
%   From SERVO_TELEMETRY (pprzlink 2026-11; older logs have no servo telemetry): t, id (actuator index),
%   position [rad], speed [rad/s or m/s], force [N or Nm], current [A], voltage [V], temperature [°C], errors.
%   Values older than 2.54 s (age 255) are NaN. Without servo telemetry s.t is empty.
%   See also pprz.esc, pprz.telemetry_rows.
    names = {'position', 'speed', 'force', 'current', 'voltage', 'temperature', 'errors'};
    if pprz.has(d, 'SERVO_TELEMETRY')
        s = pprz.telemetry_rows(d.SERVO_TELEMETRY, [names; names]', names);
    else
        s.t = zeros(0, 1);
        s.id = zeros(0, 1);
        for k = 1:numel(names)
            s.(names{k}) = zeros(0, 1);
        end
    end
end
