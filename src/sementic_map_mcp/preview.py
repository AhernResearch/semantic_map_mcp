"""Model-facing search previews; the raw MapTools search contract stays unchanged."""
from .agent import model_context, clean_model_context


def call_for_model(service, tool, arguments, image_packing='montage'):
    args = {k: v for k, v in arguments.items() if k not in ('page', 'view', 'include_types')}
    raw = service.call(tool, args)
    context = model_context(raw.envelope, arguments)
    preview = None
    media = raw.media
    if tool == 'search_candidates' and context['data'] is not None:
        if service.descriptions is not None:
            missing, records = [], []
            for candidate in context['data']['candidates']:
                oid = candidate['object_id']
                description = service.descriptions.get(oid)
                candidate['visual_description'] = description
                if description:
                    records.append(service.descriptions.records[oid])
                else:
                    missing.append(oid)
            context['data']['missing_description_ids'] = missing
            if missing:
                context['status'] = 'partial'
                context['errors'].append(dict(code='description_unavailable',
                    message='Offline object description unavailable; inspect this object if needed', affected_ids=missing))
            return raw, context, (), dict(kind='descriptions', records=records, missing_ids=missing,
                model=service.descriptions.data['model'], scene_sha256=service.descriptions.data['scene_sha256'])
        objects = [service.store.objects[c['object_id']] for c in context['data']['candidates']]
        preview, media = service.renderer.candidate_sheet(objects, image_packing)
        if preview is not None:
            context['data']['preview'] = clean_model_context({k: v for k, v in preview.items() if k != 'media'})
            if preview['media_id'] is None:
                context['data']['preview'].pop('media_id')
            context['media'] = [clean_model_context(m.metadata) for m in media]
            if preview['missing_ids']:
                context['status'] = 'partial'
                context['errors'].append(dict(code='asset_missing', message='No associated images',
                                              affected_ids=preview['missing_ids']))
    return raw, context, media, preview
