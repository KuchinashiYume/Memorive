"""Wire schemas for the existing auxiliary extraction contract."""
def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
def array(items):return {'type':'array','items':items}
STRING={'type':'string'}
QUOTE=obj({'quote':STRING,'chunk_id':STRING})
AUX_SCHEMA=obj({
    'comparison_context':array(obj({'object':STRING,'condition':STRING,'groups':array(STRING),
        'scale':STRING,'time':STRING,'scenario':STRING,'applicability':STRING})),
    'author_limitations_outlook':obj({'limitations':array(QUOTE),'outlook':array(QUOTE)}),
    'terms':array(obj({'term':STRING,'definition':STRING})),
    'citations':array(obj({'ref':STRING,'relation':STRING})),
})
