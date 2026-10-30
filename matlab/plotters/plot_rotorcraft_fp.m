function plot_rotorcraft_fp(ac_data)
%PLOT_ROTORCRAFT_FP Position against its target (east, north, up), attitude and speeds (ROTORCRAFT_FP).
    fp = ac_data.ROTORCRAFT_FP;
    axs = pprz.tiles(5, 'Position, attitude and speed');
    axes_ = {'east', 'East [m]'; 'north', 'North [m]'; 'up', 'Up [m]'};
    for k = 1:3
        plot(axs(k), fp.t, fp.(axes_{k, 1}), 'DisplayName', 'Measured');
        plot(axs(k), fp.t, fp.(['carrot_' axes_{k, 1}]), '--', 'DisplayName', 'Target');
        ylabel(axs(k), axes_{k, 2});
    end
    legend(axs(1), 'Location', 'best');
    pprz.series(axs(4), fp.t, [fp.phi, fp.theta, unwrap_deg(fp.psi), unwrap_deg(fp.carrot_psi)], ...
        {'Phi', 'Theta', 'Psi', 'Target psi'});
    ylabel(axs(4), 'Attitude [deg]');
    pprz.series(axs(5), fp.t, [hypot(fp.veast, fp.vnorth), fp.vup], {'Ground speed', 'Vertical speed'});
    if pprz.has(ac_data, 'AIR_DATA')
        plot(axs(5), ac_data.AIR_DATA.t, ac_data.AIR_DATA.airspeed, 'DisplayName', 'Airspeed');
        legend(axs(5), 'Location', 'best');
    end
    ylabel(axs(5), 'Speed [m/s]');
end

function a = unwrap_deg(a)
    a = rad2deg(unwrap(deg2rad(double(a))));
end
