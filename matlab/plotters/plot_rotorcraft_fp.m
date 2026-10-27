function plot_rotorcraft_fp(ac_data, vert)
    if ~isfield(ac_data, 'ROTORCRAFT_FP')
        return
    end

    % Plot the Rotorcraft FP
    ax1 = subplot(3,1,1);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.east, ac_data.ROTORCRAFT_FP.north, ac_data.ROTORCRAFT_FP.up, ac_data.ROTORCRAFT_FP.carrot_east, ac_data.ROTORCRAFT_FP.carrot_north, ac_data.ROTORCRAFT_FP.carrot_up]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Position target');
    xlabel('Time [s]');
    legend('East [m]', 'North [m]', 'Up [m]', 'Target east [m]', 'Target north [m]', 'Target up [m]');

    ax2 = subplot(3,1,2);
    plot(ac_data.ROTORCRAFT_FP.timestamp, [ac_data.ROTORCRAFT_FP.phi, ac_data.ROTORCRAFT_FP.theta, ac_data.ROTORCRAFT_FP.psi, ac_data.ROTORCRAFT_FP.carrot_psi]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Angles');
    xlabel('Time [s]');
    legend('Phi [deg]', 'Theta [deg]', 'Psi [deg]', 'Target psi [deg]');


    ax3 = subplot(3,1,3);
    groundspeed = sqrt(ac_data.ROTORCRAFT_FP.veast.^2.+ac_data.ROTORCRAFT_FP.vnorth.^2);
    % Try to add airspeed
    if isfield(ac_data, 'AIR_DATA')
        warning('off', 'MATLAB:linearinter:noextrap')
        resamp_airspeed = resample(timeseries(ac_data.AIR_DATA.airspeed, ac_data.AIR_DATA.timestamp), ac_data.ROTORCRAFT_FP.timestamp);
        plot(ac_data.ROTORCRAFT_FP.timestamp, [groundspeed, resamp_airspeed.Data, ac_data.ROTORCRAFT_FP.veast, ac_data.ROTORCRAFT_FP.vnorth, ac_data.ROTORCRAFT_FP.vup]);
    else
        plot(ac_data.ROTORCRAFT_FP.timestamp, [groundspeed, ac_data.ROTORCRAFT_FP.veast, ac_data.ROTORCRAFT_FP.vnorth, ac_data.ROTORCRAFT_FP.vup]);
    end
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Speeds');
    xlabel('Time [s]');
    if isfield(ac_data, 'AIR_DATA')
        legend('Ground speed [m/s]', 'Airspeed [m/s]', 'Speed east [m/s]', 'Sspeed north [m/s]', 'Speed up [m/s]');
    else
        legend('Ground speed [m/s]', 'Speed east [m/s]', 'Speed north [m/s]', 'Speed up [m/s]');
    end

    sgtitle('Rotorcraft FP measurements')
    linkaxes([ax1,ax2,ax3],'x')
end