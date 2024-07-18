function plot_guidance_indi_hybrid(ac_data)
    if ~isfield(ac_data, 'GUIDANCE_INDI_HYBRID')
        return
    end
    
    % Plot the acceleration tracking
    ax1 = subplot(3,1,1);
    hold on
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_x)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_y)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.sp_accel_z)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_x)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_y)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.accelned_filt_z)
    title('guidance INDI sp accel')
    legend('spx','spy','spz','x','y','z')
    xlabel('Time [s]');

    ax2 = subplot(3,1,2);
    hold on;
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_x)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_y)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.euler_cmd_z)
    title('guidance INDI eul cmd')
    legend('x','y','z')
    xlabel('Time [s]');

    % Plot speed in horizontal body frame
    psi_gi = interp1(ac_data.ROTORCRAFT_FP.timestamp, deg2rad(ac_data.ROTORCRAFT_FP.psi_alt), ac_data.ROTORCRAFT_FP.timestamp, 'nearest'); % Quaternion on the datarange
    speedsp_x_c = cos(psi_gi) .* ac_data.GUIDANCE_INDI_HYBRID.speed_sp_x + sin(psi_gi) .* ac_data.GUIDANCE_INDI_HYBRID.speed_sp_y;
    speedsp_y_c =-sin(psi_gi) .* ac_data.GUIDANCE_INDI_HYBRID.speed_sp_x + cos(psi_gi) .* ac_data.GUIDANCE_INDI_HYBRID.speed_sp_y;
    
    speed_x_c = cos(deg2rad(ac_data.ROTORCRAFT_FP.psi_alt)) .* ac_data.ROTORCRAFT_FP.vnorth_alt + sin(deg2rad(ac_data.ROTORCRAFT_FP.psi_alt)) .* ac_data.ROTORCRAFT_FP.veast_alt;
    speed_y_c =-sin(deg2rad(ac_data.ROTORCRAFT_FP.psi_alt)) .* ac_data.ROTORCRAFT_FP.vnorth_alt + cos(deg2rad(ac_data.ROTORCRAFT_FP.psi_alt)) .* ac_data.ROTORCRAFT_FP.veast_alt;

    ax3 = subplot(3,1,3);
    hold on;
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,speed_x_c)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,speed_y_c)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,speedsp_x_c)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,speedsp_y_c)
    plot(ac_data.ROTORCRAFT_FP.timestamp,-ac_data.ROTORCRAFT_FP.vup_alt)
    plot(ac_data.GUIDANCE_INDI_HYBRID.timestamp,ac_data.GUIDANCE_INDI_HYBRID.speed_sp_z)
    title('guidance INDI speed')
    legend('speed x', 'speed y', 'speed sp x', 'speed sp y', 'speed z','speed sp zlabel')
    xlabel('Time [s]');

    sgtitle('Guidance INDI')
    linkaxes([ax1,ax2,ax3],'x')
end