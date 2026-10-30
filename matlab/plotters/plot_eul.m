function plot_eul(ac_data, order)
%PLOT_EUL Euler angles of the attitude and its reference (AHRS_REF_QUAT), order 'ZYX' or 'ZXY' (hybrid
%   aircraft). By default the order whose roll matches ROTORCRAFT_FP.phi, else ZYX. No toolbox needed.
    m = ac_data.AHRS_REF_QUAT;
    [t, keep] = unique(m.t);
    q = double([m.body_qi(keep), m.body_qx(keep), m.body_qy(keep), m.body_qz(keep)]);
    if nargin < 2
        order = 'ZYX';
        if pprz.has(ac_data, 'ROTORCRAFT_FP') && numel(t) > 1
            fp = ac_data.ROTORCRAFT_FP;
            [ft, fk] = unique(fp.t);
            phi = interp1(ft, double(fp.phi(fk)), t, 'linear', NaN);
            zyx = euler(q, 'ZYX');
            zxy = euler(q, 'ZXY');
            ok = isfinite(phi);
            if mean(abs(zxy(ok, 1) - phi(ok))) < mean(abs(zyx(ok, 1) - phi(ok)))
                order = 'ZXY';
            end
        end
    end
    body = euler(q, order);
    ref = euler(double([m.ref_qi(keep), m.ref_qx(keep), m.ref_qy(keep), m.ref_qz(keep)]), order);
    axs = pprz.tiles(3, sprintf('Euler angles (%s)', order));
    names = {'Phi', 'Theta', 'Psi'};
    for k = 1:3
        plot(axs(k), t, body(:, k), 'DisplayName', names{k});
        plot(axs(k), t, ref(:, k), '--', 'DisplayName', [names{k} ' reference']);
        ylabel(axs(k), sprintf('%s [deg]', names{k}));
        legend(axs(k), 'Location', 'best');
    end
end

function e = euler(q, order)
    % [phi theta psi] in degrees of the NED-to-body quaternions [qi qx qy qz]; psi unwrapped.
    q = q ./ sqrt(sum(q .^ 2, 2));
    [qi, qx, qy, qz] = deal(q(:, 1), q(:, 2), q(:, 3), q(:, 4));
    r11 = 1 - 2 * (qy .^ 2 + qz .^ 2);
    r12 = 2 * (qx .* qy + qi .* qz);
    r13 = 2 * (qx .* qz - qi .* qy);
    r21 = 2 * (qx .* qy - qi .* qz);
    r22 = 1 - 2 * (qx .^ 2 + qz .^ 2);
    r23 = 2 * (qy .* qz + qi .* qx);
    r33 = 1 - 2 * (qx .^ 2 + qy .^ 2);
    if strcmpi(order, 'ZXY')
        e = [asin(max(-1, min(1, r23))), atan2(-r13, r33), atan2(-r21, r22)];
    else
        e = [atan2(r23, r33), -asin(max(-1, min(1, r13))), atan2(r12, r11)];
    end
    e(:, 3) = unwrap(e(:, 3));
    e = rad2deg(e);
end
