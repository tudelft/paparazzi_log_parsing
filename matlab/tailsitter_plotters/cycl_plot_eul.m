function cycl_plot_eul(ac_data, order)
    if ~isfield(ac_data, 'AHRS_REF_QUAT')
        return
    end

    if ~exist('order','var')
        order = 'ZYX';
    end
    
    % Plot the Euler angles
    msg = ac_data.AHRS_REF_QUAT;

    quat = double([msg.body_qi msg.body_qx msg.body_qy msg.body_qz]);
    refquat = double([msg.ref_qi msg.ref_qx msg.ref_qy msg.ref_qz]);
    [refquat_t,irefquat_t,~] = unique(msg.timestamp);
    quat = quat(irefquat_t,:);
    refquat = refquat(irefquat_t,:);

    if strcmp(order,'ZXY')
        [psi, phi, theta] = quat2angle(quat,order);
        [refpsi, refphi, reftheta] = quat2angle(refquat,order);
    elseif strcmp(order,'ZYX')
        [psi, theta, phi] = quat2angle(quat,order);
        [refpsi, reftheta, refphi] = quat2angle(refquat,order);
    else
        disp('Rotation order not available')
    end

    tiledlayout(3, 1, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on; zoom on;
    h2 = plot(refquat_t, rad2deg(reftheta), LineWidth=1.5);
    h1 = plot(refquat_t, rad2deg(theta), LineWidth=1.5);
    xlabel('Time [s]');
    ylabel('Pitch Angle [deg]');
    title('Theta');
    grid on;

    ax2 = nexttile;
    hold on; zoom on;
    h4 = plot(refquat_t, rad2deg(refphi), LineWidth=1.5);
    h3 = plot(refquat_t, rad2deg(phi), LineWidth=1.5);
    xlabel('Time [s]');
    ylabel('Roll Angle [deg]');
    title('Phi');
    grid on;

    ax3 = nexttile;
    hold on; zoom on;
    h6 = plot(refquat_t, rad2deg(refpsi), LineWidth=1.5);
    h5 = plot(refquat_t, rad2deg(psi), LineWidth=1.5);
    xlabel('Time [s]');
    ylabel('Yaw Angle [deg]');
    title('Psi');
    grid on;

    % flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_draw_mode_transitions(mode_values, mode_timestamps, {ax1, ax2, ax3});
    legend(ax1, [h1, h2], {'Theta', 'Theta Ref'});
    legend(ax2, [h3, h4], {'Phi', 'Phi ref'});
    legend(ax3, [h5, h6], {'Psi', 'Psi ref'});

    linkaxes([ax1,ax2,ax3],'x');

end